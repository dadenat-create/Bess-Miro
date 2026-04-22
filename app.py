import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import io

st.set_page_config(layout="wide")
st.title("⚡ BESS Optimizer – Advanced Logic (No MILP)")

# =========================
# PARAMETRI
# =========================
st.sidebar.header("Parametri")

P_max = st.sidebar.number_input("Potenza BESS (MW)", value=2.5)
E_max = st.sidebar.number_input("Energia BESS (MWh)", value=5.0)

SoC_min = st.sidebar.number_input("SoC min (%)", value=10.0)/100
SoC_max = st.sidebar.number_input("SoC max (%)", value=100.0)/100

eta_rt = st.sidebar.number_input("Efficienza round trip (%)", value=90.0)/100
eta = np.sqrt(eta_rt)

P_grid_out = st.sidebar.number_input("Limite prelievo (MW)", value=9.0)
P_grid_in = st.sidebar.number_input("Limite immissione (MW)", value=7.0)

oneri = st.sidebar.number_input("Oneri (€/MWh)", value=75.0)

# =========================
# FILE INPUT
# =========================
st.header("📂 Upload dati")

file_prezzi = st.file_uploader("Prezzi")
file_pv = st.file_uploader("PV")
file_load = st.file_uploader("Load")

def read_file(file):
    if file.name.endswith(".csv"):
        return pd.read_csv(file).iloc[:,0]
    else:
        return pd.read_excel(file).iloc[:,0]

# =========================
# MODELLO AVANZATO
# =========================
def simulate(prices, pv, load):

    T = len(prices)

    soc_pv = np.zeros(T)
    soc_grid = np.zeros(T)

    charge_grid = np.zeros(T)
    charge_pv = np.zeros(T)
    discharge_grid = np.zeros(T)
    discharge_load_pv = np.zeros(T)
    discharge_load_grid = np.zeros(T)

    pv_to_load = np.minimum(pv, load)
    pv_excess = np.maximum(0, pv - load)

    price_low = np.percentile(prices, 30)
    price_high = np.percentile(prices, 70)

    soc_pv[0] = SoC_min * E_max
    soc_grid[0] = 0

    for t in range(1, T):

        # =========================
        # CARICA DA FV
        # =========================
        available_charge = P_max
        cap_remaining = SoC_max*E_max - (soc_pv[t-1] + soc_grid[t-1])

        charge_pv[t] = min(pv_excess[t], available_charge, cap_remaining)
        available_charge -= charge_pv[t]

        # =========================
        # CARICA DA RETE (solo se prezzo basso)
        # =========================
        if prices[t] < price_low:
            grid_available = P_grid_out - load[t]
            charge_grid[t] = min(available_charge, grid_available, cap_remaining)

        # =========================
        # SCARICA (priorità: prezzo alto)
        # =========================
        available_discharge = P_max

        if prices[t] > price_high:

            # scarica da grid → rete
            discharge_grid[t] = min(
                soc_grid[t-1],
                available_discharge,
                P_grid_in - pv[t]
            )
            available_discharge -= discharge_grid[t]

        # scarica per autoconsumo (prima FV)
        load_gap = load[t] - pv_to_load[t]

        discharge_load_pv[t] = min(
            soc_pv[t-1],
            load_gap,
            available_discharge
        )

        available_discharge -= discharge_load_pv[t]

        discharge_load_grid[t] = min(
            soc_grid[t-1] - discharge_grid[t],
            load_gap - discharge_load_pv[t],
            available_discharge
        )

        # =========================
        # UPDATE SOC
        # =========================
        soc_pv[t] = soc_pv[t-1] + charge_pv[t]*eta - discharge_load_pv[t]/eta
        soc_grid[t] = soc_grid[t-1] + charge_grid[t]*eta - (discharge_grid[t] + discharge_load_grid[t])/eta

        # limiti
        soc_pv[t] = max(0, soc_pv[t])
        soc_grid[t] = max(0, soc_grid[t])

        total_soc = soc_pv[t] + soc_grid[t]

        if total_soc > SoC_max*E_max:
            excess = total_soc - SoC_max*E_max
            soc_grid[t] -= excess

    df = pd.DataFrame({
        "Prezzo": prices,
        "PV": pv,
        "Load": load,
        "Charge_PV": charge_pv,
        "Charge_Grid": charge_grid,
        "Discharge_Grid": discharge_grid,
        "Discharge_Load_PV": discharge_load_pv,
        "Discharge_Load_Grid": discharge_load_grid,
        "SoC_PV": soc_pv,
        "SoC_Grid": soc_grid
    })

    # =========================
    # ECONOMIA
    # =========================
    df["Revenue_market"] = df["Discharge_Grid"] * df["Prezzo"]
    df["Cost_grid"] = df["Charge_Grid"] * df["Prezzo"]
    df["Opportunity_PV"] = df["Charge_PV"] * df["Prezzo"]
    df["Saving_oneri"] = df["Discharge_Load_PV"] * oneri

    df["Profit"] = (
        df["Revenue_market"]
        - df["Cost_grid"]
        - df["Opportunity_PV"]
        + df["Saving_oneri"]
    )

    return df

# =========================
# EXPORT
# =========================
def export_excel(df):
    output = io.BytesIO()
    df.to_excel(output, index=False)
    return output.getvalue()

# =========================
# RUN
# =========================
if file_prezzi and file_pv and file_load:

    prices = read_file(file_prezzi).dropna().values
    pv = read_file(file_pv).dropna().values
    load = read_file(file_load).dropna().values

    T = min(len(prices), len(pv), len(load))

    prices = prices[:T]
    pv = pv[:T]
    load = load[:T]

    df = simulate(prices, pv, load)

    # KPI
    st.header("📊 KPI")

    col1, col2, col3 = st.columns(3)
    col1.metric("💰 Profit totale", round(df["Profit"].sum(),2))
    col2.metric("⚡ Ricavi mercato", round(df["Revenue_market"].sum(),2))
    col3.metric("🔋 Cicli eq.", round((df["Discharge_Grid"].sum()+df["Discharge_Load_PV"].sum())/(2*E_max),2))

    # grafici
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=df["Prezzo"], name="Prezzo"))
    fig.add_trace(go.Bar(y=df["Charge_PV"], name="Carica FV"))
    fig.add_trace(go.Bar(y=df["Charge_Grid"], name="Carica rete"))
    fig.add_trace(go.Bar(y=df["Discharge_Grid"], name="Scarica rete"))
    st.plotly_chart(fig, use_container_width=True)

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(y=df["SoC_PV"] + df["SoC_Grid"], name="SoC totale"))
    st.plotly_chart(fig2, use_container_width=True)

    st.dataframe(df)

    st.download_button("📥 Scarica Excel", data=export_excel(df), file_name="bess_output.xlsx")

else:
    st.info("Carica tutti i file")
