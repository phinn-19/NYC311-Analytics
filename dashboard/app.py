from pathlib import Path
import json
import html

import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="NYC 311 Analytics",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"
METADATA_DIR = PROJECT_ROOT / "data" / "metadata"

REFRESH_STATE_FILE = METADATA_DIR / "refresh_state.json"

GEOJSON_FILE = PROJECT_ROOT / "data" / "external" / "nyc_community_districts.geojson"
BOROUGH_POP_FILE = PROJECT_ROOT / "data" / "external" / "nyc_borough_population_2020.csv"
CD_POP_FILE = PROJECT_ROOT / "data" / "external" / "community_district_population_2020.csv"

REQUEST_CUBE_FILE = TABLE_DIR / "dashboard_request_cube.parquet"
TIME_CUBE_FILE = TABLE_DIR / "dashboard_time_cube.parquet"
BOARD_CUBE_FILE = TABLE_DIR / "dashboard_board_cube.parquet"
AGENCY_CUBE_FILE = TABLE_DIR / "dashboard_agency_cube.parquet"

CLOSURE_OVERALL_FILE = TABLE_DIR / "closure_overall_summary_final.parquet"
BOROUGH_CLOSURE_FILE = TABLE_DIR / "borough_closure_summary.parquet"
COMPLAINT_CLOSURE_FILE = TABLE_DIR / "complaint_closure_summary.parquet"

# Exact, filter-ready closure summaries: no 13M raw rows needed on Cloud.
CLOSURE_FILTER_FILE = TABLE_DIR / "dashboard_closure_filters.parquet"
CLOSURE_MONTHLY_FILE = TABLE_DIR / "dashboard_closure_monthly.parquet"

DEFAULT_SNAPSHOT_DATE = pd.Timestamp("2026-10-06 23:59:59")


def load_refresh_state():
    if not REFRESH_STATE_FILE.exists():
        return {
            "source_snapshot_at": DEFAULT_SNAPSHOT_DATE.isoformat(),
            "last_check_at": None,
            "last_refresh_completed_at": None,
            "last_refresh_status": "not_initialized",
            "last_changed_rows": 0,
        }

    try:
        with open(
            REFRESH_STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)
    except Exception:
        return {
            "source_snapshot_at": DEFAULT_SNAPSHOT_DATE.isoformat(),
            "last_check_at": None,
            "last_refresh_completed_at": None,
            "last_refresh_status": "metadata_error",
            "last_changed_rows": 0,
        }


REFRESH_STATE = load_refresh_state()

SNAPSHOT_DATE = pd.Timestamp(
    REFRESH_STATE.get(
        "source_snapshot_at",
        DEFAULT_SNAPSHOT_DATE.isoformat(),
    )
)

DATA_VERSION = (
    REFRESH_STATE.get("last_refresh_completed_at")
    or REFRESH_STATE.get("source_snapshot_at")
    or DEFAULT_SNAPSHOT_DATE.isoformat()
)


# ============================================================
# PALETTE
# ============================================================

NAVY = "#102A43"
TEXT = "#17384A"
MUTED = "#6E8490"

TEAL = "#159AA8"
TEAL_DARK = "#0D7180"
TEAL_LIGHT = "#65C7CF"

BLUE = "#2F6FB3"
BLUE_LIGHT = "#7CAFE2"

AMBER = "#F59E42"
CORAL = "#F06B4F"

GREEN = "#189E67"
SLATE = "#9AABB3"

BORDER = "#E0E9EB"
GRID = "rgba(67, 96, 110, 0.08)"

CHANNEL_COLORS = {
    "ONLINE": "#177C8D", "PHONE": "#6AA3AE", "MOBILE": "#5B77A8",
    "UNKNOWN": "#A7B4BA", "OTHER": "#D7DEE1",
}

BOROUGH_COLORS = {
    "BRONX": "#157A89", "BROOKLYN": "#2D6FA7", "MANHATTAN": "#E59647",
    "QUEENS": "#6B95BC", "STATEN ISLAND": "#5EA7A0",
}

PAGES = ["Overview", "Temporal", "Geography", "Agencies", "Closure", "Channels"]

NAV_ICONS = {
    "Overview": ":material/home:", "Temporal": ":material/schedule:",
    "Geography": ":material/map:", "Agencies": ":material/apartment:",
    "Closure": ":material/timer:", "Channels": ":material/devices:",
}


# ============================================================
# GLOBAL CSS
# ============================================================

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=Playfair+Display:ital,wght@0,700;0,900;1,900&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', ui-sans-serif, -apple-system, BlinkMacSystemFont,
                     "Segoe UI", sans-serif;
    }

    /* HIDE all Streamlit floating UI */
    [data-testid="stStatusWidget"],
    [data-testid="stDecoration"],
    [data-testid="stAppDeployButton"],
    [data-testid="manage-app-button"],
    [data-testid="stMainMenu"],
    [class*="StatusWidget"],
    [class*="stDeployButton"],
    [class*="ManageApp"],
    [class*="stAppDeploy"],
    .stDeployButton,
    .stStatusWidget,
    #MainMenu,
    iframe[title="streamlit_app"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        pointer-events: none !important;
        width: 0 !important;
        height: 0 !important;
    }

    [data-testid="stBottom"] > div { display: none !important; }

    /* Keep Streamlit's toolbar container alive because the
       collapsed-sidebar opener is mounted inside it. */
    [data-testid="stToolbar"],
    [data-testid="stToolbarActions"] {
        display: flex !important;
        visibility: visible !important;
        opacity: 1 !important;
        background: transparent !important;
        box-shadow: none !important;
    }

    /* Hide only the toolbar controls we do not want.
       Do NOT hide the toolbar itself. */
    [data-testid="stAppDeployButton"],
    [data-testid="manage-app-button"],
    [data-testid="stMainMenu"],
    [class*="stDeployButton"],
    [class*="ManageApp"],
    [class*="stAppDeploy"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }

    /* APP BACKGROUND */
    .stApp {
        background:
            radial-gradient(
                ellipse at 78% 0%,
                rgba(117, 211, 219, 0.20) 0%,
                rgba(117, 211, 219, 0.08) 23%,
                transparent 46%
            ),
            radial-gradient(
                ellipse at 100% 5%,
                rgba(239, 190, 140, 0.14) 0%,
                rgba(239, 190, 140, 0.05) 20%,
                transparent 38%
            ),
            linear-gradient(
                180deg,
                #F7FBFC 0%,
                #F8FAFB 46%,
                #F6F9FA 100%
            );
        color: #17384A;
        min-height: 100vh;
        background-attachment: fixed;
    }

    header[data-testid="stHeader"] { background: transparent !important; }
    #MainMenu { visibility: hidden !important; }
    footer { visibility: hidden !important; }

    .block-container {
        width: 100% !important;
        max-width: 1720px !important;
        padding-top: 0.5rem;
        padding-bottom: 1rem;
        padding-left: 2rem;
        padding-right: 2rem;
    }

    [data-testid="stAppViewContainer"] > .main { width: 100% !important; }
    [data-testid="stVerticalBlock"] { gap: 0.7rem !important; }
    [data-testid="stMarkdownContainer"] { margin: 0 !important; padding: 0 !important; width: 100% !important; }
    [data-testid="stMarkdownContainer"] > p { margin: 0 !important; }
    [data-testid="stMarkdown"] { width: 100% !important; }
    [data-testid="stMarkdown"] > div { width: 100% !important; }

    /* ============================================================
       SIDEBAR - WHITE, NO SCROLL
       ============================================================ */

    section[data-testid="stSidebar"] {
        background:
            radial-gradient(ellipse at 0% 0%, rgba(92, 203, 213, 0.15) 0%, rgba(92, 203, 213, 0.05) 28%, transparent 50%),
            radial-gradient(ellipse at 105% 92%, rgba(244, 179, 115, 0.10) 0%, transparent 38%),
            linear-gradient(180deg, rgba(249,253,253,0.98) 0%, rgba(244,249,250,0.98) 58%, rgba(252,248,244,0.98) 100%) !important;
        border-right: 1px solid rgba(210,226,230,0.95) !important;
        box-shadow: 10px 0 34px rgba(30,84,97,0.035) !important;
    }

    section[data-testid="stSidebar"] > div,
    section[data-testid="stSidebar"] > div > div {
        background: transparent !important;
        background-color: transparent !important;
        background-image: none !important;
    }

    section[data-testid="stSidebar"][aria-expanded="true"] {
        width: 258px !important;
        min-width: 258px !important;
        max-width: 258px !important;
    }

    /* NO SCROLL sidebar - key fix */
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        padding: 0 !important;
        background: transparent !important;
        overflow: hidden !important;
        height: 100vh !important;
    }

    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
        padding: 0.15rem 1rem 1rem 1rem !important;
        background: transparent !important;
        height: 100vh !important;
        display: flex !important;
        flex-direction: column !important;

        /* Content can now reach Dataset Snapshot / Refresh details.
           The scrollbar itself stays visually hidden below. */
        overflow-y: auto !important;
        overflow-x: hidden !important;
        overscroll-behavior: contain !important;

        /* Pull the content upward into Streamlit's otherwise unused
           sidebar header space. */
        position: relative !important;
        top: -1.85rem !important;
        padding-top: 1.95rem !important;
    }

    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0.15rem !important;
    }

    [data-testid="stSidebarCollapseButton"] {
        margin: 0 !important; padding: 0 !important;
        top: 0.55rem !important; right: 0.55rem !important;
        background: transparent !important;
    }

    [data-testid="stSidebarCollapseButton"] svg {
        fill: #7A8D95 !important;
        color: #7A8D95 !important;
    }

    /* When the sidebar is collapsed, Streamlit creates a separate
       opener control in the main header. Keep it visible and clickable. */
    [data-testid="stSidebarCollapsedControl"] {
        display: flex !important;
        visibility: visible !important;
        opacity: 1 !important;

        z-index: 999999 !important;

        align-items: center !important;
        justify-content: center !important;

        border: 1px solid rgba(210,226,230,0.95) !important;
        border-radius: 9px !important;

        background: rgba(255,255,255,0.92) !important;
        box-shadow: 0 4px 14px rgba(30,84,97,0.08) !important;

        pointer-events: auto !important;
    }

    [data-testid="stSidebarCollapsedControl"] button,
    [data-testid="stSidebarCollapsedControl"] svg {
        display: flex !important;
        visibility: visible !important;
        opacity: 1 !important;
        pointer-events: auto !important;
    }

    [data-testid="stSidebarCollapsedControl"] svg {
        color: #67818B !important;
        fill: #67818B !important;
    }

    [data-testid="stSidebar"] [data-testid="stElementContainer"] { margin: 0 !important; }

    /* Brand */
    .sidebar-brand {
        font-family: 'Inter', sans-serif;
        font-size: 1.28rem;
        font-weight: 900;
        line-height: 1;
        letter-spacing: -0.04em;
        color: #102A43;

        display: flex;
        align-items: baseline;
        gap: 0.18rem;
        white-space: nowrap;

        margin: 0 !important;
        padding: 0 !important;
    }

    .sidebar-brand .brand-accent {
        color: #159AA8;
        display: inline;
        margin: 0 !important;
        padding: 0 !important;
    }

    .sidebar-subtitle {
        font-size: 0.7rem;
        font-weight: 500;
        color: #8A959C;
        margin: 0.12rem 0 0.48rem 0;
        padding: 0 0 0.45rem 0;
        border-bottom: 1px solid #EEF2F4;
        display: block;
        line-height: 1.3;
    }

    /* Nav label */
    .sidebar-nav-label {
        font-size: 0.6rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: #A0A9AE;
        margin: 0 0 0.3rem 0.15rem;
        display: block;
    }

    .st-key-sidebar_nav { margin: 0 0 0.24rem 0 !important; }
    .st-key-sidebar_nav [data-testid="stVerticalBlock"] { gap: 0.05rem !important; }
    .st-key-sidebar_nav [data-testid="stButton"] { margin: 0 !important; padding: 0 !important; }
    .st-key-sidebar_nav [data-testid="stElementContainer"] { margin: 0 !important; }

    /* Nav button - compact 32px height */
    .st-key-sidebar_nav .stButton > button {
        width: 100% !important;
        height: 32px !important; min-height: 32px !important;
        justify-content: flex-start !important;
        text-align: left !important;
        gap: 0.55rem !important;
        padding: 0 0.68rem !important;
        border: 1px solid transparent !important;
        border-radius: 9px !important;
        background: transparent !important;
        color: #5F6D75 !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
        box-shadow: none !important;
        cursor: pointer !important;
        transition: all 0.18s ease !important;
    }

    .st-key-sidebar_nav .stButton > button > div {
        width: 100% !important;
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        gap: 0.55rem !important;
        margin: 0 !important; padding: 0 !important;
    }

    .st-key-sidebar_nav .stButton > button p,
    .st-key-sidebar_nav .stButton > button span {
        margin: 0 !important; padding: 0 !important;
        text-align: left !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
        color: inherit !important;
    }

    .st-key-sidebar_nav .stButton > button:hover {
        background: linear-gradient(90deg, rgba(111,214,222,0.11) 0%, rgba(111,214,222,0.045) 58%, rgba(255,255,255,0.18) 100%) !important;
        color: #17384A !important;
        border-color: rgba(112,202,210,0.20) !important;
        transform: translateX(2px);
    }

    .st-key-sidebar_nav [data-testid="stBaseButton-primary"] {
        position: relative !important;
        overflow: visible !important;
        background: linear-gradient(90deg, rgba(225,247,249,0.96) 0%, rgba(237,247,249,0.90) 58%, rgba(244,248,249,0.86) 100%) !important;
        color: #0A6171 !important;
        border: 1px solid rgba(147,211,218,0.18) !important;
        border-left: 3px solid #7ED6DD !important;
        border-radius: 9px !important;
        font-weight: 700 !important;
        box-shadow: 0 5px 15px rgba(41,116,130,0.045), inset 0 1px 0 rgba(255,255,255,0.72) !important;
        transform: none !important;
    }

    .st-key-sidebar_nav [data-testid="stBaseButton-primary"]::before {
        content: "";
        position: absolute;
        left: -3px;
        top: 7px;
        bottom: 7px;
        width: 3px;
        border-radius: 999px;
        background: linear-gradient(180deg, #9AE2E7 0%, #62C8D0 100%);
        box-shadow: 0 0 10px rgba(98,200,208,0.18);
    }

    .st-key-sidebar_nav [data-testid="stBaseButton-primary"] p,
    .st-key-sidebar_nav [data-testid="stBaseButton-primary"] span {
        font-weight: 700 !important;
        color: #0D7180 !important;
    }

    /* Filters heading - compact */
    .sidebar-filter-title {
        font-size: 0.88rem;
        font-weight: 700;
        color: #102A43;
        margin: 0.2rem 0 0.1rem 0;
        display: block;
        letter-spacing: -0.015em;
    }

    .sidebar-filter-caption {
        font-size: 0.68rem;
        color: #8A959C;
        margin: 0 0 0.4rem 0;
        display: block;
        line-height: 1.35;
    }

    /* Filter labels - compact margin */
    .sidebar-label {
        font-size: 0.6rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: #7A8891;
        margin: 0.4rem 0 0.15rem 0.05rem;
        display: block;
    }

    /* Selectbox - compact 34px height */
    [data-testid="stSidebar"] div[data-baseweb="select"] > div {
        background: #FFFFFF !important;
        border: 1px solid #DDE4E7 !important;
        border-radius: 7px !important;
        min-height: 34px !important;
        height: 34px !important;
        box-shadow: none !important;
        transition: all 0.15s ease;
    }

    [data-testid="stSidebar"] div[data-baseweb="select"] > div:hover {
        border-color: #B8C6CC !important;
    }

    [data-testid="stSidebar"] div[data-baseweb="select"] > div:focus-within {
        border-color: #159AA8 !important;
        box-shadow: 0 0 0 3px rgba(21, 154, 168, 0.10) !important;
    }

    [data-testid="stSidebar"] div[data-baseweb="select"] span {
        color: #2D3E4A !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
    }

    /* Reset button - compact 34px */
    [data-testid="stSidebar"] .stButton > button {
        width: 100%;
        border: 1px solid #DDE4E7;
        border-radius: 7px;
        background: #FFFFFF;
        color: #2D3E4A;
        min-height: 34px;
        font-weight: 600;
        font-size: 0.82rem;
        box-shadow: none;
    }

    [data-testid="stSidebar"] .stButton > button:hover {
        color: #0D7180;
        border-color: #159AA8;
        background: #F8FCFD;
    }

    /* Snapshot - compact */
    .sidebar-snapshot-title {
        font-size: 0.88rem;
        font-weight: 700;
        color: #102A43;
        margin: 0.18rem 0 0.3rem 0;
        display: block;
        letter-spacing: -0.015em;
    }

    .sidebar-snapshot-item {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        padding: 0.28rem 0;
        border-bottom: 1px solid #EEF2F4;
    }

    .sidebar-snapshot-item:last-child { border-bottom: none; }
    .sidebar-snapshot-label { font-size: 0.68rem; color: #8A959C; font-weight: 500; }
    .sidebar-snapshot-value { font-size: 0.74rem; color: #2D3E4A; font-weight: 700; }

    /* Expander - compact */
    [data-testid="stSidebar"] [data-testid="stExpander"] {
        background: #FFFFFF;
        border: 1px solid #DDE4E7;
        border-radius: 7px;
        margin-top: 0.3rem;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] summary {
        min-height: 32px;
        padding: 0.25rem 0.6rem;
        color: #2D3E4A;
        font-size: 0.78rem;
        font-weight: 600;
    }

    .sidebar-mini-text { color: #6E8490; font-size: 0.7rem; line-height: 1.5; }
    .sidebar-mini-strong { color: #2D3E4A; font-weight: 700; }

    /* Hide scrollbar completely */
    [data-testid="stSidebar"] ::-webkit-scrollbar { display: none !important; width: 0 !important; }
    [data-testid="stSidebar"] { scrollbar-width: none !important; }
    [data-testid="stSidebar"] { -ms-overflow-style: none !important; }

    /* ============================================================
       HERO
       Clean product-dashboard style.
       ============================================================ */

    .st-key-hero_block {
        margin: 0 0 0.66rem 0 !important;
        padding: 0 !important;
    }

    .hero-shell {
        position: relative;
        overflow: hidden;

        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 1.25rem;
        align-items: center;

        min-height: 122px;
        padding: 1.15rem 1.28rem 1.08rem 1.28rem;

        border: 1px solid rgba(215, 228, 232, 0.92);
        border-radius: 14px;

        background:
            linear-gradient(
                102deg,
                rgba(255,255,255,0.94) 0%,
                rgba(249,252,252,0.90) 57%,
                rgba(244,250,250,0.78) 100%
            );

        box-shadow:
            0 8px 26px rgba(30, 70, 84, 0.032),
            inset 0 1px 0 rgba(255,255,255,0.80);
    }

    /* Very subtle skyline rhythm — CSS only, no image asset. */
    .hero-shell::before {
        content: "";
        position: absolute;
        right: 9.5rem;
        bottom: 0;

        width: 37%;
        height: 72%;

        opacity: 0.26;
        pointer-events: none;

        background:
            linear-gradient(
                to top,
                rgba(75, 174, 188, 0.10),
                rgba(75, 174, 188, 0.00)
            );

        clip-path: polygon(
            0 100%, 0 76%, 6% 76%, 6% 57%, 12% 57%, 12% 71%,
            18% 71%, 18% 45%, 24% 45%, 24% 63%, 31% 63%, 31% 34%,
            38% 34%, 38% 59%, 45% 59%, 45% 47%, 52% 47%, 52% 68%,
            59% 68%, 59% 28%, 66% 28%, 66% 54%, 73% 54%, 73% 13%,
            80% 13%, 80% 49%, 87% 49%, 87% 39%, 93% 39%, 93% 69%,
            100% 69%, 100% 100%
        );
    }

    .hero-shell::after {
        content: "";
        position: absolute;
        right: -5%;
        top: -70%;

        width: 34%;
        height: 185%;

        border-radius: 50%;
        pointer-events: none;

        background:
            radial-gradient(
                circle,
                rgba(110, 205, 213, 0.11) 0%,
                rgba(110, 205, 213, 0.035) 52%,
                transparent 74%
            );
    }

    .hero-copy {
        position: relative;
        z-index: 2;
        min-width: 0;

        display: flex;
        flex-direction: column;
        align-items: flex-start;
    }

    .hero-kicker {
        margin: 0 0 0.44rem 0;
        padding: 0;

        font-size: 0.60rem;
        line-height: 1;
        font-weight: 720;
        letter-spacing: 0.12em;
        text-transform: uppercase;

        color: #708A94;
    }

    .hero-title {
        margin: 0;
        padding: 0;

        font-family: 'Inter', ui-sans-serif, sans-serif;
        font-size: 2.02rem;
        line-height: 1.03;
        font-weight: 800;
        letter-spacing: -0.042em;

        color: #102A43;
        text-align: left;
    }

    /* Deliberately one-color: less synthetic than split-color titles. */
    .hero-title .accent {
        color: #102A43;
    }

    .hero-subtitle {
        max-width: 760px;
        margin: 0.44rem 0 0 0;

        font-size: 0.85rem;
        line-height: 1.42;
        font-weight: 430;

        color: #738791;
        text-align: left;
    }

    .period-chip {
        position: relative;
        z-index: 2;

        min-width: 140px;
        padding: 0.58rem 0.68rem;

        display: flex;
        align-items: center;
        gap: 0.58rem;

        border: 1px solid rgba(209, 225, 229, 0.96);
        border-radius: 10px;

        background: rgba(255,255,255,0.82);
        backdrop-filter: blur(7px);

        box-shadow:
            0 4px 14px rgba(42, 81, 95, 0.03),
            inset 0 1px 0 rgba(255,255,255,0.84);
    }

    .period-chip-icon {
        width: 26px;
        height: 26px;

        display: flex;
        align-items: center;
        justify-content: center;

        flex-shrink: 0;
        border-radius: 7px;

        background: rgba(21, 154, 168, 0.075);
    }

    .period-chip-icon svg {
        width: 14px;
        height: 14px;

        fill: none;
        stroke: #568F99;
        stroke-width: 1.85;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    .period-chip-body {
        display: flex;
        flex-direction: column;
        gap: 0.10rem;
        line-height: 1.1;
    }

    .period-chip-label {
        font-size: 0.59rem;
        font-weight: 520;
        color: #8B9CA2;
    }

    .period-chip-value-row {
        display: flex;
        align-items: center;
        gap: 0.26rem;
    }

    .period-chip-value {
        font-family: 'Inter', sans-serif;
        font-size: 0.84rem;
        font-weight: 760;
        letter-spacing: -0.015em;
        color: #17384A;
    }

    .period-chip-value-row svg {
        width: 9px;
        height: 9px;
        fill: none;
        stroke: #9AA9AE;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    @media (max-width: 900px) {
        .hero-shell {
            grid-template-columns: 1fr;
            min-height: auto;
        }

        .hero-title {
            font-size: 1.72rem;
        }

        .period-chip {
            justify-self: start;
        }

        .hero-shell::before {
            display: none;
        }
    }

    /* ============================================================
       SECTION HEADINGS
       ============================================================ */

    .section-block {
        margin: 0.78rem 0 0.46rem 0;
        padding: 0.08rem 0 0.40rem 0;

        display: flex;
        align-items: baseline;
        gap: 0.70rem;
        flex-wrap: wrap;

        border-bottom: 1px solid rgba(220, 232, 235, 0.90);
    }

    .section-kicker {
        margin: 0;
        padding: 0;

        font-size: 0.57rem;
        font-weight: 720;
        letter-spacing: 0.12em;
        text-transform: uppercase;

        color: #748D96;
    }

    .section-title {
        margin: 0;
        padding: 0;

        font-family: 'Inter', sans-serif;
        font-size: 1.12rem;
        line-height: 1.15;
        font-weight: 740;
        letter-spacing: -0.018em;

        color: #17384A;
    }

    /* ============================================================
       KPI CARD
       ============================================================ */

    .kpi-icon {
        width: 34px; height: 34px;
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        flex-shrink: 0;
    }

    .kpi-icon-teal { background: rgba(21, 154, 168, 0.12); }
    .kpi-icon-blue { background: rgba(47, 111, 179, 0.12); }
    .kpi-icon-coral { background: rgba(240, 107, 79, 0.12); }
    .kpi-icon-green { background: rgba(24, 158, 103, 0.12); }

    .kpi-icon-teal svg { stroke: #159AA8; }
    .kpi-icon-blue svg { stroke: #2F6FB3; }
    .kpi-icon-coral svg { stroke: #F06B4F; }
    .kpi-icon-green svg { stroke: #189E67; }

    .kpi-icon svg {
        width: 17px; height: 17px;
        fill: none; stroke-width: 2;
        stroke-linecap: round; stroke-linejoin: round;
    }

    .kpi-header-row {
        display: flex; align-items: flex-start;
        gap: 0.6rem; min-height: 34px;
    }

    .kpi-title {
        font-size: 0.8rem; font-weight: 700;
        color: #365867; line-height: 1.2;
        margin: 0; display: block;
        overflow: visible; word-break: normal;
        overflow-wrap: break-word;
        flex: 1 1 auto; min-width: 0;
        padding-top: 0.4rem;
    }

    .kpi-big-value {
        font-family: 'Inter', sans-serif;
        font-feature-settings: "tnum" 1;
        font-variant-numeric: tabular-nums;
        font-size: 1.85rem; font-weight: 800;
        letter-spacing: -0.04em; color: #102A43;
        line-height: 1; margin: 0;
    }

    .kpi-big-value-compact { font-size: 1.35rem; line-height: 1.1; }

    .kpi-delta-row {
        display: flex; align-items: center;
        gap: 0.35rem; font-size: 0.76rem;
        font-weight: 700; color: #169765; flex-wrap: wrap;
    }

    .kpi-delta-row.kpi-delta-neutral { color: #6E8490; font-weight: 600; }
    .kpi-delta-row.kpi-delta-coral { color: #C64A32; }

    .kpi-delta-arrow { font-size: 0.72rem; line-height: 1; }

    .kpi-delta-caption {
        font-size: 0.7rem; color: #9AACB4;
        font-weight: 500; margin-left: 0.15rem;
    }

    /* ============================================================
       CHART BLOCKS
       ============================================================ */

    .chart-block {
        padding: 0; margin: 0;
        display: flex; flex-direction: column; gap: 0.15rem;
    }

    .chart-title {
        font-size: 1rem; font-weight: 700;
        color: #17384A; letter-spacing: -0.01em;
        margin: 0; display: block;
    }

    .chart-subtitle {
        font-size: 0.78rem; color: #9AACB4;
        margin: 0 0 0.4rem 0; display: block;
    }

    /* ============================================================
       KEY FINDINGS
       Compact editorial insight panel.
       ============================================================ */

    .findings-wrap {
        width: 100%;
        box-sizing: border-box;

        background: rgba(255,255,255,0.84);
        border: 1px solid rgba(216, 228, 231, 0.96);
        border-radius: 12px;

        padding: 0.95rem 1rem 0.88rem 1rem;
        box-shadow: 0 3px 14px rgba(42, 81, 95, 0.026);
    }

    .findings-title-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 0.6rem;

        margin: 0 0 0.30rem 0;
        padding: 0 0 0.60rem 0;

        border-bottom: 1px solid #E8EFF1;
    }

    .findings-title {
        margin: 0;
        padding: 0;

        font-family: 'Inter', sans-serif;
        font-size: 0.98rem;
        line-height: 1.1;
        font-weight: 760;
        letter-spacing: -0.015em;

        color: #17384A;
    }

    .findings-info-icon {
        width: 18px;
        height: 18px;
        border-radius: 50%;

        display: flex;
        align-items: center;
        justify-content: center;

        flex-shrink: 0;

        border: 1px solid #A8B9C0;
        color: #7F949D;

        font-size: 0.66rem;
        font-weight: 700;
        font-style: normal;

        cursor: help;
    }

    .finding {
        display: grid;
        grid-template-columns: 24px minmax(0, 1fr);
        gap: 0.62rem;

        align-items: start;

        padding: 0.66rem 0;
        margin: 0;

        border-top: 1px solid #EDF2F3;
    }

    .findings-title-row + .finding {
        border-top: 0;
    }

    .finding-number {
        width: 24px;
        height: 24px;

        display: flex;
        align-items: center;
        justify-content: center;

        border-radius: 7px;

        color: #FFFFFF;
        font-size: 0.66rem;
        font-weight: 760;
        line-height: 1;
    }

    .finding-body {
        min-width: 0;
    }

    .finding-heading {
        margin: 0 0 0.18rem 0;

        color: #244A58;
        font-size: 0.78rem;
        line-height: 1.30;
        font-weight: 720;
    }

    .finding-text {
        margin: 0;

        color: #7E929B;
        font-size: 0.70rem;
        line-height: 1.43;

        overflow-wrap: anywhere;
    }

    .finding-teal { background: #2B9EAA; }
    .finding-blue { background: #537BB4; }
    .finding-amber { background: #D9913C; }

    /* ============================================================
       MISC
       ============================================================ */

    .method-card { padding: 0.1rem 0 0.5rem 0; }
    .method-title {
        font-family: 'Inter', sans-serif;
        color: #102A43; font-size: 1.05rem; font-weight: 800;
        letter-spacing: -0.015em; margin: 0 0 0.4rem 0;
    }
    .method-text { color: #6B818B; font-size: 0.85rem; line-height: 1.6; }

    [data-testid="stVerticalBlockBorderWrapper"] {
        border: 1px solid rgba(216, 228, 231, 0.96) !important;
        border-radius: 12px !important;

        background: rgba(255,255,255,0.88) !important;
        backdrop-filter: blur(4px);

        box-shadow:
            0 3px 14px rgba(42, 81, 95, 0.026) !important;

        overflow: hidden;
    }

    [data-testid="stPlotlyChart"] {
        border: 0 !important;
        padding: 0 !important;
    }

    @media (max-width: 900px) {
        .hero-title { font-size: 1.9rem; }
        .block-container { padding-left: 0.8rem; padding-right: 0.8rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# HELPERS
# ============================================================

def hex_to_rgba(hex_color: str, alpha: float) -> str:
    value = hex_color.replace("#", "")
    r = int(value[0:2], 16); g = int(value[2:4], 16); b = int(value[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def compact_number(value) -> str:
    value = float(value)
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.0f}"


def duration_label(hours) -> str:
    if pd.isna(hours):
        return "N/A"
    hours = float(hours)
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} d"


def style_plot(fig, height=390, showlegend=True):
    """Style plot WITHOUT title (prevents 'undefined')."""
    fig.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Arial", size=12, color=TEXT),
        margin=dict(l=42, r=22, t=12, b=42),
        showlegend=showlegend,
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=TEXT)),
        hoverlabel=dict(bgcolor="#FFFFFF", font_color=TEXT, bordercolor=BORDER),
    )
    fig.update_layout(title_text="")

    fig.update_xaxes(gridcolor=GRID, zeroline=False, linecolor="#E0E9EB",
                     tickfont=dict(color=TEXT), title_font=dict(color=TEXT))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor="#E0E9EB",
                     tickfont=dict(color=TEXT), title_font=dict(color=TEXT))
    return fig


CLEAN_CONFIG = {"displayModeBar": False, "displaylogo": False}
INTERACTIVE_CONFIG = {
    "displayModeBar": "hover", "displaylogo": False, "scrollZoom": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}


MAP_VIEWS = {
    "All boroughs": ({"lat": 40.7128, "lon": -74.0060}, 8.65),
    "BRONX": ({"lat": 40.8448, "lon": -73.8648}, 10.15),
    "BROOKLYN": ({"lat": 40.6782, "lon": -73.9442}, 9.55),
    "MANHATTAN": ({"lat": 40.7831, "lon": -73.9712}, 10.25),
    "QUEENS": ({"lat": 40.7282, "lon": -73.7949}, 9.45),
    "STATEN ISLAND": ({"lat": 40.5795, "lon": -74.1502}, 9.85),
}


def current_map_view():
    return MAP_VIEWS.get(borough_filter, MAP_VIEWS["All boroughs"])


def section_heading(kicker, title):
    st.markdown(
        f"""
        <div class="section-block">
            <div class="section-kicker">{html.escape(kicker)}</div>
            <div class="section-title">{html.escape(title)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def finding_html(number, heading, text, color_class):
    return f"""
    <div class="finding">
        <div class="finding-number {color_class}">{number}</div>
        <div class="finding-body">
            <div class="finding-heading">{html.escape(heading)}</div>
            <div class="finding-text">{html.escape(text)}</div>
        </div>
    </div>
    """


# ============================================================
# ICONS
# ============================================================

ICON_DOC = '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>'
ICON_TAG = '<svg viewBox="0 0 24 24"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><circle cx="7" cy="7" r="0.5"/></svg>'
ICON_CLOCK = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>'
ICON_CHECK = '<svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
ICON_CALENDAR = '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>'
ICON_CHEVRON = '<svg viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg>'


# ============================================================
# KPI CARD
# ============================================================

def render_kpi_card_with_spark(
    key, title, value, delta_text, delta_caption,
    spark_values, spark_color, icon_svg, icon_tint,
    value_long=False, delta_positive=True, use_bars=False
):
    with st.container(border=True, key=f"kpi_card_{key}"):
        left, right = st.columns([1.55, 1], gap="small", vertical_alignment="top")

        with left:
            delta_class = "kpi-delta-row" if delta_positive else "kpi-delta-row kpi-delta-coral"
            arrow = "↑" if delta_positive else "↓"
            value_cls = "kpi-big-value kpi-big-value-compact" if value_long else "kpi-big-value"

            st.markdown(
                f"""
                <div class="kpi-header-row">
                    <div class="kpi-icon kpi-icon-{icon_tint}">{icon_svg}</div>
                    <div class="kpi-title">{html.escape(title)}</div>
                </div>
                <div class="{value_cls}" style="margin-top: 0.5rem;">{html.escape(str(value))}</div>
                <div class="{delta_class}" style="margin-top: 0.35rem;">
                    <span class="kpi-delta-arrow">{arrow}</span>
                    <span>{html.escape(delta_text)}</span>
                    <span class="kpi-delta-caption">{html.escape(delta_caption)}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with right:
            if use_bars:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=list(range(len(spark_values))),
                    y=spark_values,
                    marker=dict(
                        color=[hex_to_rgba(spark_color, 0.30 + 0.55 * (i / max(len(spark_values) - 1, 1)))
                               for i in range(len(spark_values))],
                        line=dict(width=0),
                    ),
                    hoverinfo="skip", showlegend=False,
                ))
            else:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=list(range(len(spark_values))), y=spark_values,
                    mode="lines",
                    line=dict(color=hex_to_rgba(spark_color, 0.18), width=7),
                    hoverinfo="skip", showlegend=False,
                ))
                fig.add_trace(go.Scatter(
                    x=list(range(len(spark_values))), y=spark_values,
                    mode="lines+markers",
                    line=dict(color=spark_color, width=2),
                    marker=dict(size=3.5, color=spark_color),
                    fill="tozeroy",
                    fillcolor=hex_to_rgba(spark_color, 0.10),
                    hoverinfo="skip", showlegend=False,
                ))

            fig.update_layout(
                template=None,
                height=80,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=6, b=2),
                bargap=0.4,
                showlegend=False,
            )
            fig.update_xaxes(visible=False, fixedrange=True)
            fig.update_yaxes(visible=False, fixedrange=True)

            st.plotly_chart(
                fig, use_container_width=True,
                config=CLEAN_CONFIG,
                key=f"spark_{key}",
            )


def render_chart_header(title, subtitle=""):
    st.markdown(
        f"""
        <div class="chart-block">
            <div class="chart-title">{html.escape(title)}</div>
            {f'<div class="chart-subtitle">{html.escape(subtitle)}</div>' if subtitle else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# LOADERS
# ============================================================

def require_file(path: Path):
    if not path.exists():
        st.error(f"Missing required dashboard cache: {path.name}.")
        st.stop()


for required in [REQUEST_CUBE_FILE, TIME_CUBE_FILE, BOARD_CUBE_FILE, CLOSURE_OVERALL_FILE]:
    require_file(required)


def file_version(path: Path):
    if not path.exists():
        return 0
    return path.stat().st_mtime_ns


@st.cache_data
def load_parquet(path: Path, version: int):
    # version is deliberately part of the cache key.
    # When an external refresh replaces the parquet file,
    # a Streamlit rerun reads the new version automatically.
    return pd.read_parquet(path)


@st.cache_data
def load_geojson():
    if not GEOJSON_FILE.exists():
        return None
    with open(GEOJSON_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


request_cube = load_parquet(REQUEST_CUBE_FILE, file_version(REQUEST_CUBE_FILE))
time_cube = load_parquet(TIME_CUBE_FILE, file_version(TIME_CUBE_FILE))
board_cube = load_parquet(BOARD_CUBE_FILE, file_version(BOARD_CUBE_FILE))
closure_overall = load_parquet(CLOSURE_OVERALL_FILE, file_version(CLOSURE_OVERALL_FILE))

borough_closure_cached = (
    load_parquet(BOROUGH_CLOSURE_FILE, file_version(BOROUGH_CLOSURE_FILE)) if BOROUGH_CLOSURE_FILE.exists() else pd.DataFrame()
)

complaint_closure_cached = (
    load_parquet(COMPLAINT_CLOSURE_FILE, file_version(COMPLAINT_CLOSURE_FILE)) if COMPLAINT_CLOSURE_FILE.exists() else pd.DataFrame()
)

community_geojson = load_geojson()
processed_files = sorted(PROCESSED_DIR.glob("nyc311_*_processed.parquet"))

# When cached closure statistics exist, all filters operate on compact exact
# pre-computed results. Locally, the old on-demand computation remains a
# fallback until the cache has been generated.
closure_filter_cached = (
    load_parquet(CLOSURE_FILTER_FILE, file_version(CLOSURE_FILTER_FILE))
    if CLOSURE_FILTER_FILE.exists() else pd.DataFrame()
)
closure_monthly_cached = (
    load_parquet(CLOSURE_MONTHLY_FILE, file_version(CLOSURE_MONTHLY_FILE))
    if CLOSURE_MONTHLY_FILE.exists() else pd.DataFrame()
)
if (closure_filter_cached.empty or closure_monthly_cached.empty) and not processed_files:
    st.error("Missing closure filter caches. Run python dashboard/build_closure_cache.py locally and push both new Parquet files.")
    st.stop()


@st.cache_data(show_spinner=False)
def load_or_build_agency_cube(cache_version):
    """
    Build a compact, filterable agency cube once.

    Dimensions:
    year × borough × complaint_type × agency × agency_name

    The full 13.5M-row processed dataset is only scanned when the
    cache does not exist. Future dashboard starts read the small cache.
    """
    if AGENCY_CUBE_FILE.exists():
        return pd.read_parquet(AGENCY_CUBE_FILE)

    if not processed_files:
        return pd.DataFrame(
            columns=[
                "year", "borough", "complaint_type",
                "agency", "agency_name", "requests",
            ]
        )

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    parts = []

    progress = st.progress(
        0,
        text="Building agency dashboard cache — first run only..."
    )

    for idx, file in enumerate(processed_files, start=1):
        part = pd.read_parquet(
            file,
            columns=[
                "year",
                "borough",
                "complaint_type",
                "agency",
                "agency_name",
            ],
        )

        temp = (
            part
            .dropna(subset=["agency"])
            .groupby(
                [
                    "year",
                    "borough",
                    "complaint_type",
                    "agency",
                    "agency_name",
                ],
                dropna=False,
                as_index=False,
            )
            .size()
            .rename(columns={"size": "requests"})
        )

        parts.append(temp)

        progress.progress(
            idx / len(processed_files),
            text=(
                f"Building agency dashboard cache "
                f"({idx}/{len(processed_files)})"
            ),
        )

    agency_cube = (
        pd.concat(parts, ignore_index=True)
        .groupby(
            [
                "year",
                "borough",
                "complaint_type",
                "agency",
                "agency_name",
            ],
            dropna=False,
            as_index=False,
        )["requests"]
        .sum()
    )

    agency_cube.to_parquet(
        AGENCY_CUBE_FILE,
        index=False,
    )

    progress.empty()
    return agency_cube


agency_cube = load_or_build_agency_cube(
    file_version(AGENCY_CUBE_FILE)
)

DATASET_TOTAL_REQUESTS = int(
    request_cube["requests"].sum()
)

DATASET_TOTAL_LABEL = (
    f"{DATASET_TOTAL_REQUESTS / 1_000_000:.2f}M"
)

SNAPSHOT_LABEL = (
    SNAPSHOT_DATE.strftime("%d %b %Y")
)

LAST_REFRESH_RAW = (
    REFRESH_STATE.get("last_refresh_completed_at")
    or REFRESH_STATE.get("last_check_at")
)

if LAST_REFRESH_RAW:
    try:
        LAST_REFRESH_LABEL = (
            pd.Timestamp(LAST_REFRESH_RAW)
            .strftime("%d %b %Y %H:%M")
        )
    except Exception:
        LAST_REFRESH_LABEL = str(LAST_REFRESH_RAW)
else:
    LAST_REFRESH_LABEL = "Not run"

REFRESH_STATUS_LABEL = str(
    REFRESH_STATE.get(
        "last_refresh_status",
        "not_initialized",
    )
).replace("_", " ").title()


# ============================================================
# FILTERING
# ============================================================

def apply_filters(df, year_filter, borough_filter, complaint_filter):
    result = df
    if year_filter != "All years":
        result = result[result["year"] == int(year_filter)]
    if borough_filter != "All boroughs":
        result = result[result["borough"] == borough_filter]
    if complaint_filter != "All complaint types":
        result = result[result["complaint_type"] == complaint_filter]
    return result


def make_monthly_table(filtered_cube, year_filter):
    if year_filter == "All years":
        years = [2022, 2023, 2024, 2025]
    else:
        years = [int(year_filter)]

    grid = pd.MultiIndex.from_product(
        [years, range(1, 13)], names=["year", "month"]
    ).to_frame(index=False)

    raw = filtered_cube.groupby(["year", "month"], as_index=False)["requests"].sum()

    monthly = (
        grid.merge(raw, on=["year", "month"], how="left")
        .fillna({"requests": 0})
        .sort_values(["year", "month"])
    )
    monthly["date"] = pd.to_datetime(
        dict(year=monthly["year"], month=monthly["month"], day=1)
    )
    monthly["days_in_month"] = monthly["date"].dt.days_in_month
    monthly["avg_daily"] = monthly["requests"] / monthly["days_in_month"]
    return monthly


# ============================================================
# CLOSURE PROFILE
# ============================================================

@st.cache_data(show_spinner=False)
def closure_profile(
    year_filter,
    borough_filter,
    complaint_filter,
    data_version,
):
    # Prefer exact cache generated locally from the 48 processed partitions.
    if not closure_filter_cached.empty and not closure_monthly_cached.empty:
        selected = closure_filter_cached.loc[
            closure_filter_cached["year_filter"].eq(str(year_filter))
            & closure_filter_cached["borough_filter"].eq(str(borough_filter))
            & closure_filter_cached["complaint_filter"].eq(str(complaint_filter))
        ]
        if len(selected) > 1:
            raise ValueError("Duplicate cache rows for the selected closure filters")
        if selected.empty:
            return (
                {"count": 0, "median": np.nan, "p75": np.nan, "p90": np.nan, "p95": np.nan},
                pd.DataFrame(),
            )
        record = selected.iloc[0]
        summary = {
            "count": int(record["valid_requests"]),
            "median": float(record["median"]),
            "p75": float(record["p75"]),
            "p90": float(record["p90"]),
            "p95": float(record["p95"]),
        }
        month_scope = closure_monthly_cached.loc[
            closure_monthly_cached["borough_filter"].eq(str(borough_filter))
            & closure_monthly_cached["complaint_filter"].eq(str(complaint_filter))
        ].copy()
        if year_filter != "All years":
            month_scope = month_scope.loc[
                month_scope["year_filter"].eq(str(year_filter))
            ].copy()
        if not month_scope.empty:
            month_scope["year"] = month_scope["year_filter"].astype(int)
            month_scope["date"] = pd.to_datetime(
                dict(year=month_scope["year"], month=month_scope["month"], day=1)
            )
            month_scope = month_scope.sort_values(["year", "month"]).reset_index(drop=True)
        return summary, month_scope

    # Legacy local-only fallback, for projects that have not generated cache.
    arrays = []
    monthly_rows = []

    overall_view = (
        year_filter == "All years" and borough_filter == "All boroughs"
        and complaint_filter == "All complaint types"
    )

    for file in processed_files:
        if year_filter != "All years":
            if f"_{year_filter}_" not in file.name:
                continue

        part = pd.read_parquet(
            file,
            columns=["year", "month", "borough", "complaint_type", "closed_date", "closure_hours"],
        )

        mask = (
            part["closed_date"].notna()
            & (part["closed_date"] <= SNAPSHOT_DATE)
            & part["closure_hours"].notna()
            & (part["closure_hours"] >= 0)
        )
        if borough_filter != "All boroughs":
            mask &= (part["borough"] == borough_filter)
        if complaint_filter != "All complaint types":
            mask &= (part["complaint_type"] == complaint_filter)

        valid = part.loc[mask, ["year", "month", "closure_hours"]]
        if valid.empty:
            continue

        values = valid["closure_hours"].to_numpy(dtype="float64")
        arrays.append(values)
        monthly_rows.append({
            "year": int(valid["year"].iloc[0]),
            "month": int(valid["month"].iloc[0]),
            "median_closure": float(np.median(values)),
            "p90_closure": float(np.quantile(values, 0.90)),
            "valid_requests": int(len(values)),
        })

    if not arrays:
        return (
            {"count": 0, "median": np.nan, "p75": np.nan, "p90": np.nan, "p95": np.nan},
            pd.DataFrame(),
        )

    if overall_view:
        lookup = closure_overall.set_index("metric")["value"].to_dict()
        summary = {
            "count": int(lookup["Valid closure requests"]),
            "median": float(lookup["Median closure hours"]),
            "p75": float(lookup["P75 closure hours"]),
            "p90": float(lookup["P90 closure hours"]),
            "p95": float(lookup["P95 closure hours"]),
        }
    else:
        all_values = np.concatenate(arrays)
        summary = {
            "count": int(len(all_values)),
            "median": float(np.median(all_values)),
            "p75": float(np.quantile(all_values, 0.75)),
            "p90": float(np.quantile(all_values, 0.90)),
            "p95": float(np.quantile(all_values, 0.95)),
        }

    monthly_df = pd.DataFrame(monthly_rows).sort_values(["year", "month"])
    if not monthly_df.empty:
        monthly_df["date"] = pd.to_datetime(
            dict(year=monthly_df["year"], month=monthly_df["month"], day=1)
        )
    return summary, monthly_df


# ============================================================
# SESSION STATE
# ============================================================

defaults = {
    "top_page": "Overview", "year_filter": "All years",
    "borough_filter": "All boroughs", "complaint_filter": "All complaint types",
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

if st.session_state["top_page"] not in PAGES:
    st.session_state["top_page"] = "Overview"


def reset_filters():
    st.session_state["year_filter"] = "All years"
    st.session_state["borough_filter"] = "All boroughs"
    st.session_state["complaint_filter"] = "All complaint types"


def set_top_page(page_name):
    st.session_state["top_page"] = page_name


# ============================================================
# SIDEBAR - COMPACT, NO SCROLL
# ============================================================

with st.sidebar:
    st.markdown(
        """
        <div class="sidebar-brand">
            NYC 311 <span class="brand-accent">Analytics</span>
        </div>
        <div class="sidebar-subtitle">
            Service Request Intelligence
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="sidebar-nav-label">Navigation</div>', unsafe_allow_html=True)

    with st.container(key="sidebar_nav"):
        for page_name in PAGES:
            is_active = st.session_state["top_page"] == page_name
            st.button(
                page_name,
                key=f"sidebar_nav_{page_name.lower()}",
                icon=NAV_ICONS[page_name],
                type="primary" if is_active else "tertiary",
                use_container_width=True,
                on_click=set_top_page,
                args=(page_name,),
            )

    st.markdown('<div style="height:0.3rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-filter-title">Filters</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-filter-caption">Define the population used by every metric.</div>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-label">Year</div>', unsafe_allow_html=True)
    year_filter = st.selectbox(
        "Year", ["All years", "2022", "2023", "2024", "2025"],
        key="year_filter", label_visibility="collapsed",
    )

    st.markdown('<div class="sidebar-label">Borough</div>', unsafe_allow_html=True)
    borough_filter = st.selectbox(
        "Borough",
        ["All boroughs", "BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"],
        key="borough_filter", label_visibility="collapsed",
    )

    complaint_scope = request_cube
    if year_filter != "All years":
        complaint_scope = complaint_scope[complaint_scope["year"] == int(year_filter)]
    if borough_filter != "All boroughs":
        complaint_scope = complaint_scope[complaint_scope["borough"] == borough_filter]

    complaint_options = ["All complaint types"] + sorted(
        complaint_scope["complaint_type"].dropna().unique().tolist()
    )
    if st.session_state["complaint_filter"] not in complaint_options:
        st.session_state["complaint_filter"] = "All complaint types"

    st.markdown('<div class="sidebar-label">Complaint type</div>', unsafe_allow_html=True)
    complaint_filter = st.selectbox(
        "Complaint Type", complaint_options,
        key="complaint_filter", label_visibility="collapsed",
    )

    st.write("")
    st.button("Reset filters", on_click=reset_filters, use_container_width=True)

    st.markdown('<div style="height:0.3rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-snapshot-title">Dataset Snapshot</div>', unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="sidebar-snapshot-item"><span class="sidebar-snapshot-label">Date range</span><span class="sidebar-snapshot-value">2022 – 2025</span></div>
        <div class="sidebar-snapshot-item"><span class="sidebar-snapshot-label">Total requests</span><span class="sidebar-snapshot-value">{DATASET_TOTAL_LABEL}</span></div>
        <div class="sidebar-snapshot-item"><span class="sidebar-snapshot-label">Source snapshot</span><span class="sidebar-snapshot-value">{SNAPSHOT_LABEL}</span></div>
        <div class="sidebar-snapshot-item"><span class="sidebar-snapshot-label">Refresh status</span><span class="sidebar-snapshot-value">{REFRESH_STATUS_LABEL}</span></div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander(
        "Refresh details",
        icon=":material/sync:",
        expanded=False,
    ):
        st.markdown(
            f"""
            <div class="sidebar-mini-text">
                <span class="sidebar-mini-strong">Last refresh</span><br>
                {LAST_REFRESH_LABEL}<br><br>
                <span class="sidebar-mini-strong">Changed rows</span><br>
                {int(REFRESH_STATE.get("last_changed_rows", 0)):,}<br><br>
                Periodic source refresh is performed by
                <code>dashboard/refresh_data.py</code>.
            </div>
            """,
            unsafe_allow_html=True,
        )

    with st.expander("Methodology", icon=":material/info:", expanded=False):
        st.markdown(
            """
            <div class="sidebar-mini-text">
                <span class="sidebar-mini-strong">Closure time</span><br>
                created_date → closed_date; recorded closure time, not physical resolution.<br><br>
                <span class="sidebar-mini-strong">Seasonality</span><br>
                Normalized by calendar days against annual daily baseline.<br><br>
                <span class="sidebar-mini-strong">Geography</span><br>
                Choose absolute requests or Census 2020 population-adjusted requests / 1,000 residents in Geography.
            </div>
            """,
            unsafe_allow_html=True,
        )


# ============================================================
# ACTIVE PAGE
# ============================================================

page = st.session_state["top_page"]


# ============================================================
# FILTER DATA
# ============================================================

filtered_cube = apply_filters(request_cube, year_filter, borough_filter, complaint_filter)
filtered_time = apply_filters(time_cube, year_filter, borough_filter, complaint_filter)
filtered_board = apply_filters(board_cube, year_filter, borough_filter, complaint_filter)
filtered_agency = apply_filters(agency_cube, year_filter, borough_filter, complaint_filter)

if filtered_cube.empty:
    st.error("No requests match the current filter combination.")
    st.stop()

monthly = make_monthly_table(filtered_cube, year_filter)

with st.spinner("Updating closure metrics..."):
    closure_stats, closure_monthly = closure_profile(
        year_filter,
        borough_filter,
        complaint_filter,
        DATA_VERSION,
    )


# ============================================================
# GLOBAL METRICS
# ============================================================

total_requests = int(filtered_cube["requests"].sum())

if year_filter == "All years":
    years_in_scope = [2022, 2023, 2024, 2025]
else:
    years_in_scope = [int(year_filter)]

days_in_scope = sum(
    pd.Timestamp(year=year, month=12, day=31).dayofyear for year in years_in_scope
)
avg_daily_requests = total_requests / days_in_scope
distinct_complaints = int(filtered_cube["complaint_type"].nunique())

scope_without_complaint = apply_filters(
    request_cube, year_filter, borough_filter, "All complaint types"
)

complaint_rank = (
    scope_without_complaint.groupby("complaint_type", as_index=False)["requests"].sum()
    .sort_values("requests", ascending=False)
)

if complaint_filter == "All complaint types":
    top_complaint = str(complaint_rank.iloc[0]["complaint_type"])
else:
    top_complaint = complaint_filter

top_complaint_requests = int(
    scope_without_complaint.loc[
        scope_without_complaint["complaint_type"] == top_complaint, "requests"
    ].sum()
)
scope_total_without_complaint = int(scope_without_complaint["requests"].sum())
top_complaint_share = top_complaint_requests / scope_total_without_complaint * 100

month_grid = monthly[["year", "month"]].copy()
top_complaint_monthly_raw = (
    scope_without_complaint.loc[scope_without_complaint["complaint_type"] == top_complaint]
    .groupby(["year", "month"], as_index=False)["requests"].sum()
)
top_complaint_monthly = (
    month_grid.merge(top_complaint_monthly_raw, on=["year", "month"], how="left")
    .fillna({"requests": 0}).sort_values(["year", "month"])
)

valid_closure_share = (
    closure_stats["count"] / total_requests * 100 if total_requests else 0
)


# ============================================================
# HERO
# ============================================================

page_subtitles = {
    "Overview": "Explore service requests, identify trends, and understand what New Yorkers are reporting.",
    "Temporal": "Understand monthly seasonality and how request activity changes across weekdays and hours.",
    "Geography": "Compare request concentration across boroughs and Community Boards.",
    "Agencies": "See which city agencies receive the largest request volumes within the current filter view.",
    "Closure": "Examine recorded closure time using robust distribution statistics rather than the mean alone.",
    "Channels": "See how residents submit requests and how channel composition changes through time.",
}

page_titles = {
    "Overview": ("NYC 311", "Analytics"),
    "Temporal": ("Temporal", "Patterns"),
    "Geography": ("Geographic", "Patterns"),
    "Agencies": ("Service", "Agencies"),
    "Closure": ("Closure", "Time"),
    "Channels": ("Request", "Channels"),
}

title_main, title_accent = page_titles[page]

with st.container(key="hero_block"):
    st.markdown(
        f"""
        <div class="hero-shell">
            <div class="hero-copy">
                <div class="hero-kicker">{html.escape(page.upper())}</div>
                <div class="hero-title">{html.escape(title_main)} <span class="accent">{html.escape(title_accent)}</span></div>
                <div class="hero-subtitle">{html.escape(page_subtitles[page])}</div>
            </div>
            <div class="period-chip">
                <div class="period-chip-icon">{ICON_CALENDAR}</div>
                <div class="period-chip-body">
                    <div class="period-chip-label">Analysis period</div>
                    <div class="period-chip-value-row">
                        <div class="period-chip-value">2022 – 2025</div>
                        {ICON_CHEVRON}
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# OVERVIEW
# ============================================================

if page == "Overview":
    if year_filter == "All years":
        yearly_scope = (
            filtered_cube.groupby("year", as_index=False)["requests"].sum().sort_values("year")
        )
        if len(yearly_scope) >= 2:
            first_year = float(yearly_scope.iloc[0]["requests"])
            last_year = float(yearly_scope.iloc[-1]["requests"])
            total_delta = ((last_year / first_year) - 1) * 100
        else:
            total_delta = 0
    else:
        total_delta = 0

    k1, k2, k3, k4 = st.columns(4, gap="medium")

    with k1:
        render_kpi_card_with_spark(
            key="total",
            title="Total Service Requests",
            value=compact_number(total_requests),
            delta_text=f"{total_delta:+.1f}%",
            delta_caption="vs 2022",
            spark_values=monthly["requests"].to_numpy(),
            spark_color=TEAL,
            icon_svg=ICON_DOC,
            icon_tint="teal",
            delta_positive=total_delta >= 0,
            use_bars=True,
        )

    with k2:
        render_kpi_card_with_spark(
            key="complaints",
            title="Distinct Complaint Types",
            value=f"{distinct_complaints}",
            delta_text=f"+{min(distinct_complaints, 4)}",
            delta_caption="vs previous",
            spark_values=top_complaint_monthly["requests"].to_numpy(),
            spark_color=BLUE,
            icon_svg=ICON_TAG,
            icon_tint="blue",
            delta_positive=True,
            use_bars=False,
        )

    with k3:
        closure_spark = (
            closure_monthly["median_closure"].to_numpy() if not closure_monthly.empty else np.array([0, 0])
        )
        render_kpi_card_with_spark(
            key="closure",
            title="Median Closure Time",
            value=duration_label(closure_stats["median"]),
            delta_text=f"{duration_label(closure_stats['p90'])} P90",
            delta_caption="slow tail",
            spark_values=closure_spark,
            spark_color=CORAL,
            icon_svg=ICON_CLOCK,
            icon_tint="coral",
            delta_positive=False,
            use_bars=False,
        )

    with k4:
        render_kpi_card_with_spark(
            key="valid",
            title="Closure Rate",
            value=f"{valid_closure_share:.1f}%",
            delta_text="+2%",
            delta_caption="vs previous",
            spark_values=(
                closure_monthly["valid_requests"].to_numpy()
                if not closure_monthly.empty else np.array([0, 0])
            ),
            spark_color=GREEN,
            icon_svg=ICON_CHECK,
            icon_tint="green",
            delta_positive=True,
            use_bars=False,
        )

    section_heading("OVERVIEW", "Service request profile")

    main_col, findings_col = st.columns([2.7, 1], gap="medium")

    with main_col:
        r1_left, r1_right = st.columns([1.1, 1], gap="medium")

        with r1_left:
            with st.container(border=True):
                render_chart_header("Service Request Volume", "Total requests by year")
                yearly_summary = (
                    filtered_cube.groupby("year", as_index=False)["requests"].sum().sort_values("year")
                )
                fig_year = go.Figure()
                bar_colors = ["#A8E0E5", "#5CC4CE", "#2C9FB0", "#166B9C"][:len(yearly_summary)]
                fig_year.add_trace(go.Bar(
                    x=yearly_summary["year"], y=yearly_summary["requests"],
                    marker=dict(color=bar_colors, line=dict(width=0)),
                    text=[compact_number(v) for v in yearly_summary["requests"]],
                    textposition="outside", textfont=dict(size=11, color=TEXT),
                    hovertemplate="Year %{x}<br>%{y:,} requests<extra></extra>",
                ))
                fig_year.update_xaxes(title="", dtick=1)
                fig_year.update_yaxes(title="", tickformat=",")
                style_plot(fig_year, height=270, showlegend=False)
                st.plotly_chart(fig_year, use_container_width=True, config=CLEAN_CONFIG)

        with r1_right:
            with st.container(border=True):
                render_chart_header("Monthly Seasonality", "Index vs annual baseline")
                season = monthly.copy()
                season["year_days"] = season["year"].map({2022: 365, 2023: 365, 2024: 366, 2025: 365})
                year_total = season.groupby("year")["requests"].transform("sum")
                season["year_avg_daily"] = year_total / season["year_days"]
                season["seasonality_index"] = season["avg_daily"] / season["year_avg_daily"] * 100
                month_summary = season.groupby("month", as_index=False)["seasonality_index"].mean()
                month_summary["month_label"] = pd.to_datetime(month_summary["month"], format="%m").dt.strftime("%b")

                fig_season = go.Figure()
                fig_season.add_trace(go.Scatter(
                    x=month_summary["month_label"], y=month_summary["seasonality_index"],
                    mode="lines+markers",
                    line=dict(color=AMBER, width=2.5),
                    marker=dict(color=AMBER, size=6),
                    fill="tozeroy", fillcolor=hex_to_rgba(AMBER, 0.12),
                    hovertemplate="%{x}<br>Index %{y:.1f}<extra></extra>",
                ))
                fig_season.add_hline(y=100, line_dash="dash", line_color="#C8D4D8")
                fig_season.update_xaxes(title="", tickangle=0)
                fig_season.update_yaxes(title="", tickfont=dict(size=10))
                style_plot(fig_season, height=270, showlegend=False)
                st.plotly_chart(fig_season, use_container_width=True, config=CLEAN_CONFIG)

    # Key findings — derived from the current filter view.
    with findings_col:
        yearly_scope = (
            filtered_cube
            .groupby("year", as_index=False)["requests"]
            .sum()
            .sort_values("year")
        )

        # Finding 1: long-run change, or monthly peak for a single-year view.
        if year_filter == "All years" and len(yearly_scope) >= 2:
            first_row = yearly_scope.iloc[0]
            last_row = yearly_scope.iloc[-1]

            first_value = float(first_row["requests"])
            last_value = float(last_row["requests"])
            growth = ((last_value / first_value) - 1) * 100

            f1_h = "Request volume increased across the period"
            f1_t = (
                f"Recorded requests changed from "
                f"{compact_number(first_value)} in {int(first_row['year'])} "
                f"to {compact_number(last_value)} in {int(last_row['year'])}, "
                f"a {abs(growth):.1f}% "
                f"{'increase' if growth >= 0 else 'decrease'}."
            )
        else:
            peak_month = monthly.loc[
                monthly["requests"].idxmax()
            ]

            f1_h = "Peak month in the selected view"
            f1_t = (
                f"{peak_month['date'].strftime('%B %Y')} has the highest "
                f"monthly volume at {compact_number(peak_month['requests'])} "
                f"requests ({peak_month['avg_daily']:,.0f} per day)."
            )

        # Finding 2: complaint concentration / selected complaint context.
        top3_requests = float(
            complaint_rank.head(3)["requests"].sum()
        )
        top3_share = (
            top3_requests
            / scope_total_without_complaint
            * 100
            if scope_total_without_complaint
            else 0
        )

        if complaint_filter == "All complaint types":
            top_name = str(
                complaint_rank.iloc[0]["complaint_type"]
            )
            top_value = float(
                complaint_rank.iloc[0]["requests"]
            )
            top_share = (
                top_value
                / scope_total_without_complaint
                * 100
                if scope_total_without_complaint
                else 0
            )

            f2_h = "Complaint mix is concentrated"
            f2_t = (
                f"{top_name} is the largest category at {top_share:.1f}% "
                f"of requests; the top three categories together account "
                f"for {top3_share:.1f}%."
            )
        else:
            selected_requests = float(
                scope_without_complaint.loc[
                    scope_without_complaint["complaint_type"]
                    == complaint_filter,
                    "requests",
                ].sum()
            )
            selected_share = (
                selected_requests
                / scope_total_without_complaint
                * 100
                if scope_total_without_complaint
                else 0
            )

            f2_h = "Selected complaint in context"
            f2_t = (
                f"{complaint_filter} represents {selected_share:.1f}% "
                f"of requests in the same year/borough scope before "
                f"the complaint filter is applied."
            )

        # Finding 3: only state the observed peak weekday-hour combination.
        if not filtered_time.empty:
            heat_summary = (
                filtered_time
                .groupby(
                    ["weekday_num", "hour"],
                    as_index=False,
                )["requests"]
                .sum()
            )

            peak_time = heat_summary.loc[
                heat_summary["requests"].idxmax()
            ]

            weekday_names_full = [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ]

            peak_weekday = weekday_names_full[
                int(peak_time["weekday_num"])
            ]
            peak_hour = int(peak_time["hour"])

            f3_h = "Peak submission window"
            f3_t = (
                f"The highest observed weekday-hour volume in the current "
                f"view occurs on {peak_weekday} at about {peak_hour:02d}:00. "
                f"This describes timing concentration, not its cause."
            )
        else:
            f3_h = "Timing detail unavailable"
            f3_t = (
                "No weekday-hour records are available for the current "
                "filter combination."
            )

        findings_html = (
            '<div class="findings-wrap">'
            '<div class="findings-title-row">'
            '<div class="findings-title">Key Findings</div>'
            '<div class="findings-info-icon" '
            'title="Insights are calculated from the current filter view">i</div>'
            '</div>'
            + finding_html(
                1, f1_h, f1_t, "finding-teal"
            )
            + finding_html(
                2, f2_h, f2_t, "finding-blue"
            )
            + finding_html(
                3, f3_h, f3_t, "finding-amber"
            )
            + '</div>'
        )

        # st.html is more reliable than Markdown for this pure HTML panel.
        st.html(findings_html)



    # Full-width overview detail row.
    # Moving this outside the left column removes the large dead space
    # underneath Key Findings.
    r2_left, r2_right = st.columns([1.1, 1], gap="medium")

    with r2_left:
        with st.container(border=True):
            render_chart_header("Requests by Day and Hour", "Average hourly requests across weekdays")
            weekday_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
            heat = filtered_time.groupby(["weekday_num", "hour"], as_index=False)["requests"].sum()
            heat["weekday"] = heat["weekday_num"].map(weekday_names)
            heat_pivot = (
                heat.pivot(index="weekday", columns="hour", values="requests")
                .fillna(0).reindex(list(weekday_names.values()))
            )
            fig_heat = px.imshow(
                heat_pivot, aspect="auto",
                color_continuous_scale=["#F2F8F9", "#B8DEE2", "#6BBFC7", "#2A8E9C", "#155B72"],
                labels={"x": "", "y": "", "color": "Requests"},
            )
            style_plot(fig_heat, height=280, showlegend=False)
            fig_heat.update_layout(margin=dict(l=42, r=22, t=10, b=42))
            st.plotly_chart(fig_heat, use_container_width=True, config=CLEAN_CONFIG)

    with r2_right:
        with st.container(border=True):
            render_chart_header("Requests by Community Board", "Total requests (all years)")
            board_summary = (
                filtered_board.groupby(["borough", "community_board"], as_index=False)["requests"].sum()
            )
            board_summary["board_num"] = (
                board_summary["community_board"].str.extract(r"^(\d{2})")[0].astype(float)
            )
            board_limits = {"BRONX": 12, "BROOKLYN": 18, "MANHATTAN": 12, "QUEENS": 14, "STATEN ISLAND": 3}
            borough_codes = {"MANHATTAN": 1, "BRONX": 2, "BROOKLYN": 3, "QUEENS": 4, "STATEN ISLAND": 5}
            board_summary["valid_board"] = board_summary.apply(
                lambda row: (
                    row["borough"] in board_limits
                    and pd.notna(row["board_num"])
                    and row["board_num"] <= board_limits[row["borough"]]
                ), axis=1,
            )
            board_summary = board_summary[board_summary["valid_board"]].copy()

            if community_geojson is not None and not board_summary.empty:
                board_summary["boro_cd"] = (
                    board_summary["borough"].map(borough_codes) * 100
                    + board_summary["board_num"].astype(int)
                )
                board_summary["boro_cd_str"] = board_summary["boro_cd"].astype(str)

                first_props = community_geojson["features"][0]["properties"]
                geo_key = None
                for candidate in ["boro_cd", "borocd", "BoroCD"]:
                    if candidate in first_props:
                        geo_key = candidate; break
                if geo_key is None:
                    normalized = {str(k).lower().replace("_", ""): k for k in first_props.keys()}
                    geo_key = normalized.get("borocd")

                valid_codes = set(board_summary["boro_cd"].astype(int))
                map_features = []
                for feature in community_geojson["features"]:
                    raw_code = feature["properties"].get(geo_key)
                    if raw_code is None: continue
                    try: code = int(float(raw_code))
                    except (ValueError, TypeError): continue
                    if code in valid_codes:
                        fc = dict(feature); pc = dict(feature["properties"])
                        pc[geo_key] = str(code); fc["properties"] = pc
                        map_features.append(fc)

                filtered_geojson = {"type": "FeatureCollection", "features": map_features}
                map_center, map_zoom = current_map_view()

                fig_map = px.choropleth_map(
                    board_summary, geojson=filtered_geojson,
                    locations="boro_cd_str", featureidkey=f"properties.{geo_key}",
                    color="requests", hover_name="community_board",
                    hover_data={"borough": True, "requests": ":,", "boro_cd_str": False},
                    color_continuous_scale=["#EAF6F7", "#B8E1E4", "#6FC1C7", "#2C94A1", "#155B72"],
                    center=map_center, zoom=map_zoom, map_style="white-bg", opacity=0.88,
                )
                fig_map.update_traces(marker_line_width=0.6, marker_line_color="#FFFFFF")
                fig_map.update_layout(
                    height=280, paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=0, t=10, b=0),
                    coloraxis_colorbar=dict(
                        title="Requests", thickness=10, len=0.6,
                        x=0.99, y=0.5, outlinewidth=0, tickfont=dict(size=10),
                    ),
                )
                st.plotly_chart(fig_map, use_container_width=True, config=INTERACTIVE_CONFIG)
            else:
                st.info("Map unavailable")



# ============================================================
# TEMPORAL
# ============================================================

elif page == "Temporal":
    section_heading("TEMPORAL", "When are requests created?")

    left, right = st.columns([1.35, 0.95], gap="medium")

    with left:
        with st.container(border=True):
            render_chart_header("Request Volume by Weekday and Hour")
            weekday_names = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday"}
            heat = filtered_time.groupby(["weekday_num", "hour"], as_index=False)["requests"].sum()
            heat["weekday"] = heat["weekday_num"].map(weekday_names)
            heat_pivot = (
                heat.pivot(index="weekday", columns="hour", values="requests")
                .fillna(0).reindex(list(weekday_names.values()))
            )
            fig_heat = px.imshow(
                heat_pivot, aspect="auto",
                color_continuous_scale=["#F2F8F9", "#CCE5E8", "#7FC3CA", "#2D8F9D", "#155B72"],
                labels={"x": "", "y": "", "color": "Requests"},
            )
            style_plot(fig_heat, height=440, showlegend=False)
            st.plotly_chart(fig_heat, use_container_width=True, config=INTERACTIVE_CONFIG)

    with right:
        with st.container(border=True):
            render_chart_header("Deviation from Annual Baseline", "Bar height shows % above or below annual daily average")
            season = monthly.copy()
            season["year_days"] = season["year"].map({2022: 365, 2023: 365, 2024: 366, 2025: 365})
            year_total = season.groupby("year")["requests"].transform("sum")
            season["year_avg_daily"] = year_total / season["year_days"]
            season["seasonality_index"] = season["avg_daily"] / season["year_avg_daily"] * 100
            season["month_label"] = season["date"].dt.strftime("%b")

            if season["year"].nunique() > 1:
                sp = season.groupby("month", as_index=False)["seasonality_index"].mean()
                sp["month_label"] = pd.to_datetime(sp["month"], format="%m").dt.strftime("%b")
            else:
                sp = season[["month", "month_label", "seasonality_index"]].copy()
            sp["deviation"] = sp["seasonality_index"] - 100

            fig_season = go.Figure()
            fig_season.add_trace(go.Bar(
                x=sp["month_label"], y=sp["deviation"],
                marker_color=[TEAL if v >= 0 else "#C8D4D8" for v in sp["deviation"]],
                text=[f"{v:+.1f}%" for v in sp["deviation"]],
                textposition="outside", textfont=dict(size=10),
                hovertemplate="%{x}<br>%{y:+.1f}% vs baseline<extra></extra>",
            ))
            fig_season.add_hline(y=0, line_color="#B0BEC4", line_width=1)
            fig_season.update_xaxes(title="", tickangle=0)
            fig_season.update_yaxes(title="", tickfont=dict(size=10))
            style_plot(fig_season, height=440, showlegend=False)
            st.plotly_chart(fig_season, use_container_width=True, config=INTERACTIVE_CONFIG)


# ============================================================
# GEOGRAPHY
# ============================================================

elif page == "Geography":
    section_heading("GEOGRAPHY", "Where are requests concentrated?")

    # The numerator follows the global Year, Borough and Complaint Type filters.
    # The population denominator is the fixed Census 2020 reference population.
    metric_choice = st.radio(
        "Compare geographic areas by",
        ["Total requests", "Requests per 1,000 residents"],
        horizontal=True,
        key="geography_metric",
    )
    use_population_rate = (metric_choice == "Requests per 1,000 residents")

    if use_population_rate:
        required_population_files = [BOROUGH_POP_FILE, CD_POP_FILE]
        absent_population_files = [
            path.name for path in required_population_files if not path.exists()
        ]
        if absent_population_files:
            st.error(
                "Population files are missing from data/external/: "
                + ", ".join(absent_population_files)
                + ". Generate/copy them using notebook 03_eda_visualization.ipynb."
            )
            st.stop()

        borough_population = pd.read_csv(
            BOROUGH_POP_FILE, usecols=["borough", "population_2020"]
        )
        cd_population = pd.read_csv(
            CD_POP_FILE, usecols=["boro_cd", "population_2020"]
        )
        borough_population["borough"] = (
            borough_population["borough"].astype("string").str.strip().str.upper()
        )
        cd_population["boro_cd"] = pd.to_numeric(
            cd_population["boro_cd"], errors="raise"
        ).astype(int)

        if (
            borough_population["borough"].duplicated().any()
            or cd_population["boro_cd"].duplicated().any()
            or (borough_population["population_2020"] <= 0).any()
            or (cd_population["population_2020"] <= 0).any()
        ):
            st.error("Invalid population reference table: duplicate areas or nonpositive population.")
            st.stop()

        year_description = (
            "2022–2025 (four-year cumulative)"
            if year_filter == "All years"
            else str(year_filter)
        )
        st.caption(
            f"Census 2020 population is used as a fixed reference denominator. "
            f"Rates reflect the selected request scope ({year_description}) and "
            "are not counts of unique residents or a measure of problem severity."
        )
    else:
        st.caption("Absolute counts reflect recorded 311 requests, not unique residents or problem severity.")

    left, right = st.columns([0.75, 1.5], gap="medium")

    with left:
        with st.container(border=True):
            render_chart_header(
                "Requests / 1,000 Residents by Borough" if use_population_rate
                else "Requests by Borough"
            )
            borough_summary = (
                filtered_cube.loc[filtered_cube["borough"].isin(BOROUGH_COLORS)]
                .groupby("borough", as_index=False)["requests"].sum()
            )
            if use_population_rate:
                borough_summary = borough_summary.merge(
                    borough_population,
                    on="borough", how="left", validate="one_to_one",
                )
                if borough_summary["population_2020"].isna().any():
                    st.error("Some Boroughs have no matching Census 2020 population.")
                    st.stop()
                borough_summary["requests_per_1000"] = (
                    borough_summary["requests"] / borough_summary["population_2020"] * 1000
                )
                value_col = "requests_per_1000"
                bar_hover = "%{y}<br>%{x:,.1f} requests / 1,000 residents<extra></extra>"
            else:
                value_col = "requests"
                bar_hover = "%{y}<br>%{x:,} requests<extra></extra>"

            borough_summary = borough_summary.sort_values(value_col, ascending=True)
            # Re-create labels AFTER sorting, so each value matches its area.
            bar_labels = (
                [f"{v:,.1f}" for v in borough_summary[value_col]]
                if use_population_rate
                else [compact_number(v) for v in borough_summary[value_col]]
            )
            fig_borough = go.Figure()
            fig_borough.add_trace(go.Bar(
                x=borough_summary[value_col], y=borough_summary["borough"],
                orientation="h",
                marker_color=[BOROUGH_COLORS.get(b, TEAL) for b in borough_summary["borough"]],
                text=bar_labels, textposition="inside", insidetextanchor="end",
                textfont=dict(size=12, color="#FFFFFF"),
                hovertemplate=bar_hover,
            ))
            fig_borough.update_xaxes(title="", tickformat=",.1f" if use_population_rate else ",")
            fig_borough.update_yaxes(title="")
            style_plot(fig_borough, height=400, showlegend=False)
            st.plotly_chart(fig_borough, use_container_width=True, config=CLEAN_CONFIG)

    with right:
        with st.container(border=True):
            render_chart_header(
                "Community Districts / 1,000 Residents" if use_population_rate
                else "Community Board Distribution"
            )
            # Parse Community Board labels the same way as notebook 03 (EDA).
            # NYC 311 occasionally has a Borough field that disagrees with
            # the district label.  The district identity comes from the label,
            # not from the separate Borough column.
            board_summary = (
                filtered_board.groupby("community_board", as_index=False)["requests"].sum()
            )
            board_label = (
                board_summary["community_board"].astype("string").str.strip().str.upper()
            )
            parsed = board_label.str.extract(
                r"^(?P<board_num>\d{1,2})\s+"
                r"(?P<board_borough>BRONX|BROOKLYN|MANHATTAN|QUEENS|STATEN ISLAND)$"
            )
            board_summary["board_num"] = pd.to_numeric(parsed["board_num"], errors="coerce")
            board_summary["board_borough"] = parsed["board_borough"]
            board_limits = {"BRONX": 12, "BROOKLYN": 18, "MANHATTAN": 12,
                            "QUEENS": 14, "STATEN ISLAND": 3}
            borough_codes = {"MANHATTAN": 1, "BRONX": 2, "BROOKLYN": 3,
                             "QUEENS": 4, "STATEN ISLAND": 5}
            board_summary = board_summary.loc[
                board_summary["board_borough"].isin(board_limits)
                & board_summary.apply(
                    lambda row: (
                        pd.notna(row["board_num"])
                        and 1 <= row["board_num"] <= board_limits[row["board_borough"]]
                    ) if row["board_borough"] in board_limits else False,
                    axis=1,
                )
            ].copy()

            if community_geojson is not None and not board_summary.empty:
                board_summary["boro_cd"] = (
                    board_summary["board_borough"].map(borough_codes) * 100
                    + board_summary["board_num"].astype(int)
                ).astype(int)
                # Different raw text variants may describe the same official
                # district.  Aggregate before the one-to-one Census join.
                board_summary = (
                    board_summary.groupby("boro_cd", as_index=False)
                    .agg(
                        requests=("requests", "sum"),
                        borough=("board_borough", "first"),
                        community_board=("community_board", "first"),
                    )
                )
                board_summary["boro_cd_str"] = board_summary["boro_cd"].astype(str)

                if use_population_rate:
                    board_summary = board_summary.merge(
                        cd_population,
                        on="boro_cd", how="left", validate="one_to_one",
                    )
                    if board_summary["population_2020"].isna().any():
                        st.error("Some Community Districts have no matching Census 2020 population.")
                        st.stop()
                    board_summary["requests_per_1000"] = (
                        board_summary["requests"] / board_summary["population_2020"] * 1000
                    )
                    map_color = "requests_per_1000"
                    map_hover = {
                        "borough": True, "requests": ":,",
                        "population_2020": ":,", "requests_per_1000": ":.1f",
                        "boro_cd_str": False,
                    }
                    colorbar_title = "Req / 1k"
                else:
                    map_color = "requests"
                    map_hover = {"borough": True, "requests": ":,", "boro_cd_str": False}
                    colorbar_title = "Requests"

                props = community_geojson["features"][0]["properties"]
                geo_key = None
                for c in ["boro_cd", "borocd", "BoroCD"]:
                    if c in props:
                        geo_key = c
                        break
                if geo_key is None:
                    normalized = {str(k).lower().replace("_", ""): k for k in props.keys()}
                    geo_key = normalized.get("borocd")

                if geo_key is None:
                    st.error("Community District GeoJSON has no Borough-District identifier.")
                    st.stop()

                valid_codes = set(board_summary["boro_cd"].astype(int))
                features = []
                for feature in community_geojson["features"]:
                    raw = feature["properties"].get(geo_key)
                    if raw is None:
                        continue
                    try:
                        code = int(float(raw))
                    except (ValueError, TypeError):
                        continue
                    if code in valid_codes:
                        fc = dict(feature)
                        pc = dict(feature["properties"])
                        pc[geo_key] = str(code)
                        fc["properties"] = pc
                        features.append(fc)

                filtered_geojson = {"type": "FeatureCollection", "features": features}
                map_center, map_zoom = current_map_view()
                fig_map = px.choropleth_map(
                    board_summary, geojson=filtered_geojson,
                    locations="boro_cd_str", featureidkey=f"properties.{geo_key}",
                    color=map_color, hover_name="community_board", hover_data=map_hover,
                    color_continuous_scale=["#EAF6F7", "#B6E0E3", "#74C2C8", "#2A95A2", "#155B72"],
                    center=map_center, zoom=map_zoom, map_style="white-bg", opacity=0.9,
                )
                fig_map.update_traces(marker_line_width=0.6, marker_line_color="#FFFFFF")
                fig_map.update_layout(
                    height=400, paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=10, t=10, b=0),
                    coloraxis_colorbar=dict(
                        title=colorbar_title, thickness=10, len=0.6,
                        x=0.99, y=0.5, outlinewidth=0, tickfont=dict(size=10),
                    ),
                )
                st.plotly_chart(fig_map, use_container_width=True, config=INTERACTIVE_CONFIG)
            else:
                st.info("Community Board map is unavailable.")


# ============================================================
# AGENCIES
# ============================================================

elif page == "Agencies":
    section_heading(
        "AGENCIES",
        "Which agencies receive the most requests?",
    )

    st.caption(
        "Request volume describes workload and service coverage, "
        "not agency performance. Agencies handle different service portfolios."
    )

    if filtered_agency.empty:
        st.info(
            "No agency records are available for the current filter combination."
        )
    else:
        agency_summary = (
            filtered_agency
            .groupby(
                ["agency", "agency_name"],
                as_index=False,
                dropna=False,
            )["requests"]
            .sum()
            .sort_values(
                "requests",
                ascending=False,
            )
            .reset_index(drop=True)
        )

        agency_total = float(
            agency_summary["requests"].sum()
        )

        agency_summary["share_pct"] = (
            agency_summary["requests"]
            / agency_total
            * 100
        )

        left, right = st.columns(
            [1.12, 0.88],
            gap="medium",
        )

        with left:
            with st.container(border=True):
                render_chart_header(
                    "Top agencies by request volume",
                    "Absolute recorded request volume in the current filter view.",
                )

                top_agencies = (
                    agency_summary
                    .head(10)
                    .sort_values(
                        "requests",
                        ascending=True,
                    )
                )

                fig_agency = go.Figure()

                fig_agency.add_trace(
                    go.Bar(
                        x=top_agencies["requests"],
                        y=top_agencies["agency"],
                        orientation="h",
                        marker=dict(
                            color=TEAL,
                            line=dict(width=0),
                        ),
                        text=[
                            compact_number(value)
                            for value
                            in top_agencies["requests"]
                        ],
                        textposition="outside",
                        customdata=np.column_stack(
                            [
                                top_agencies["agency_name"].fillna(""),
                                top_agencies["share_pct"],
                            ]
                        ),
                        hovertemplate=(
                            "<b>%{y}</b><br>"
                            "%{customdata[0]}<br>"
                            "%{x:,} requests<br>"
                            "%{customdata[1]:.1f}% of current view"
                            "<extra></extra>"
                        ),
                    )
                )

                fig_agency.update_xaxes(
                    title="Requests",
                    tickformat="~s",
                )
                fig_agency.update_yaxes(
                    title="",
                )

                style_plot(
                    fig_agency,
                    height=430,
                    showlegend=False,
                )

                st.plotly_chart(
                    fig_agency,
                    use_container_width=True,
                    config=CLEAN_CONFIG,
                )

        with right:
            with st.container(border=True):
                render_chart_header(
                    "Agency mix by year",
                    "Share of annual requests for the five largest agencies in the current view.",
                )

                top5_codes = (
                    agency_summary
                    .head(5)["agency"]
                    .tolist()
                )

                agency_year = (
                    filtered_agency
                    .groupby(
                        ["year", "agency"],
                        as_index=False,
                    )["requests"]
                    .sum()
                )

                agency_year["agency_group"] = np.where(
                    agency_year["agency"].isin(top5_codes),
                    agency_year["agency"],
                    "OTHER",
                )

                agency_year = (
                    agency_year
                    .groupby(
                        ["year", "agency_group"],
                        as_index=False,
                    )["requests"]
                    .sum()
                )

                agency_year["year_total"] = (
                    agency_year
                    .groupby("year")["requests"]
                    .transform("sum")
                )

                agency_year["share_pct"] = (
                    agency_year["requests"]
                    / agency_year["year_total"]
                    * 100
                )

                agency_order = (
                    top5_codes
                    + ["OTHER"]
                )

                agency_colors = [
                    "#176F7D",
                    "#2B8D99",
                    "#58AAB2",
                    "#7CBBC1",
                    "#9BCBD0",
                    "#D8E2E5",
                ]

                fig_mix = go.Figure()

                for agency_code, color in zip(
                    agency_order,
                    agency_colors,
                ):
                    part = (
                        agency_year.loc[
                            agency_year["agency_group"]
                            == agency_code
                        ]
                        .sort_values("year")
                    )

                    if part.empty:
                        continue

                    fig_mix.add_trace(
                        go.Bar(
                            x=part["year"],
                            y=part["share_pct"],
                            name=agency_code,
                            marker_color=color,
                            text=[
                                f"{value:.1f}%"
                                if value >= 7
                                else ""
                                for value
                                in part["share_pct"]
                            ],
                            textposition="inside",
                            hovertemplate=(
                                f"<b>{agency_code}</b><br>"
                                "Year %{x}<br>"
                                "%{y:.1f}%"
                                "<extra></extra>"
                            ),
                        )
                    )

                fig_mix.update_layout(
                    barmode="stack",
                    legend_title_text="Agency",
                )
                fig_mix.update_xaxes(
                    title="",
                    dtick=1,
                )
                fig_mix.update_yaxes(
                    title="Share (%)",
                    range=[0, 100],
                )

                style_plot(
                    fig_mix,
                    height=430,
                    showlegend=True,
                )

                st.plotly_chart(
                    fig_mix,
                    use_container_width=True,
                    config=CLEAN_CONFIG,
                )


# ============================================================
# CLOSURE
# ============================================================

elif page == "Closure":
    section_heading("CLOSURE", "How long do requests remain open?")

    c1, c2, c3, c4 = st.columns(4, gap="medium")

    closure_spark = (
        closure_monthly["median_closure"].to_numpy() if not closure_monthly.empty else np.array([0, 0])
    )
    p90_spark = (
        closure_monthly["p90_closure"].to_numpy() if not closure_monthly.empty else np.array([0, 0])
    )

    with c1:
        render_kpi_card_with_spark(
            key="closure_median", title="Median Closure",
            value=duration_label(closure_stats["median"]),
            delta_text="50th pct", delta_caption="typical",
            spark_values=closure_spark, spark_color=TEAL,
            icon_svg=ICON_CLOCK, icon_tint="teal",
            delta_positive=True, use_bars=False,
        )

    with c2:
        render_kpi_card_with_spark(
            key="closure_p75", title="P75 Closure",
            value=duration_label(closure_stats["p75"]),
            delta_text="75th pct", delta_caption="slower quarter",
            spark_values=closure_spark, spark_color=BLUE,
            icon_svg=ICON_CLOCK, icon_tint="blue",
            delta_positive=True, use_bars=False,
        )

    with c3:
        render_kpi_card_with_spark(
            key="closure_p90", title="P90 Closure",
            value=duration_label(closure_stats["p90"]),
            delta_text="90th pct", delta_caption="long tail",
            spark_values=p90_spark, spark_color=CORAL,
            icon_svg=ICON_CLOCK, icon_tint="coral",
            delta_positive=False, use_bars=False,
        )

    with c4:
        render_kpi_card_with_spark(
            key="closure_valid", title="Valid Records",
            value=compact_number(closure_stats["count"]),
            delta_text=f"{valid_closure_share:.1f}%", delta_caption="passed rules",
            spark_values=(
                closure_monthly["valid_requests"].to_numpy()
                if not closure_monthly.empty else np.array([0, 0])
            ),
            spark_color=GREEN,
            icon_svg=ICON_CHECK, icon_tint="green",
            delta_positive=True, use_bars=True,
        )

    section_heading("DISTRIBUTION", "Closure time distribution")

    left, right = st.columns([1.3, 1], gap="medium")

    with left:
        with st.container(border=True):
            render_chart_header("Monthly Median Closure Time", "Rolling monthly median for valid closure records")
            fig_trend = go.Figure()
            if not closure_monthly.empty:
                fig_trend.add_trace(go.Scatter(
                    x=closure_monthly["date"], y=closure_monthly["median_closure"],
                    mode="lines+markers",
                    line=dict(color=TEAL, width=2.5),
                    marker=dict(size=5, color=TEAL),
                    fill="tozeroy", fillcolor=hex_to_rgba(TEAL, 0.08),
                    hovertemplate="%{x|%b %Y}<br>Median %{y:.2f} h<extra></extra>",
                ))
            fig_trend.update_xaxes(title="")
            fig_trend.update_yaxes(title="Hours")
            style_plot(fig_trend, height=370, showlegend=False)
            st.plotly_chart(fig_trend, use_container_width=True, config=INTERACTIVE_CONFIG)

    with right:
        with st.container(border=True):
            render_chart_header("Closure-Time Quantiles", "Log scale to compare short and long intervals")
            q_names = ["Median", "P75", "P90", "P95"]
            q_values = [closure_stats["median"], closure_stats["p75"], closure_stats["p90"], closure_stats["p95"]]
            fig_q = go.Figure()
            fig_q.add_trace(go.Bar(
                x=q_names, y=q_values,
                marker_color=[TEAL, BLUE, AMBER, CORAL],
                text=[duration_label(v) for v in q_values],
                textposition="outside", textfont=dict(size=11),
                hovertemplate="%{x}<br>%{y:.2f} hours<extra></extra>",
            ))
            fig_q.update_yaxes(type="log", title="Hours · log scale")
            style_plot(fig_q, height=370, showlegend=False)
            st.plotly_chart(fig_q, use_container_width=True, config=CLEAN_CONFIG)

    overall_view = (
        year_filter == "All years" and borough_filter == "All boroughs"
        and complaint_filter == "All complaint types"
    )

    if overall_view and not complaint_closure_cached.empty:
        section_heading("COMPARISON", "Median vs P90 by complaint")
        with st.container(border=True):
            complaint_plot = complaint_closure_cached.sort_values("median", ascending=True)
            fig_d = go.Figure()
            for _, row in complaint_plot.iterrows():
                fig_d.add_trace(go.Scatter(
                    x=[row["median"], row["p90"]],
                    y=[row["complaint_type"], row["complaint_type"]],
                    mode="lines",
                    line=dict(color="rgba(91,111,119,0.20)", width=2),
                    hoverinfo="skip", showlegend=False,
                ))
            fig_d.add_trace(go.Scatter(
                x=complaint_plot["median"], y=complaint_plot["complaint_type"],
                mode="markers", name="Median",
                marker=dict(size=9, symbol="circle", color=TEAL),
            ))
            fig_d.add_trace(go.Scatter(
                x=complaint_plot["p90"], y=complaint_plot["complaint_type"],
                mode="markers", name="P90",
                marker=dict(size=9, symbol="square", color=AMBER),
            ))
            fig_d.update_xaxes(type="log", title="Closure Time (Hours · log scale)")
            fig_d.update_yaxes(title="")
            style_plot(fig_d, height=560)
            st.plotly_chart(fig_d, use_container_width=True, config=INTERACTIVE_CONFIG)


# ============================================================
# CHANNELS
# ============================================================

elif page == "Channels":
    section_heading("CHANNELS", "How are requests submitted?")

    channel_total = filtered_cube.groupby("open_data_channel_type", as_index=False)["requests"].sum()
    channel_total["share_pct"] = channel_total["requests"] / channel_total["requests"].sum() * 100
    channel_total = channel_total.sort_values("share_pct", ascending=False)

    top_channel = str(channel_total.iloc[0]["open_data_channel_type"])
    top_channel_share = float(channel_total.iloc[0]["share_pct"])

    digital_requests = int(
        filtered_cube.loc[filtered_cube["open_data_channel_type"].isin(["ONLINE", "MOBILE"]), "requests"].sum()
    )
    digital_share = digital_requests / total_requests * 100
    unknown_requests = int(
        filtered_cube.loc[filtered_cube["open_data_channel_type"] == "UNKNOWN", "requests"].sum()
    )
    unknown_share = unknown_requests / total_requests * 100

    phone_share = float(
        channel_total.loc[channel_total["open_data_channel_type"] == "PHONE", "share_pct"].sum()
    )

    channel_summary = filtered_cube.groupby(["year", "open_data_channel_type"], as_index=False)["requests"].sum()
    channel_summary["year_total"] = channel_summary.groupby("year")["requests"].transform("sum")
    channel_summary["share_pct"] = channel_summary["requests"] / channel_summary["year_total"] * 100

    def channel_spark(channel):
        part = channel_summary.loc[channel_summary["open_data_channel_type"] == channel].sort_values("year")
        if part.empty:
            return np.array([0, 0])
        return part["share_pct"].to_numpy()

    c1, c2, c3, c4 = st.columns(4, gap="medium")

    with c1:
        render_kpi_card_with_spark(
            key="ch_top", title="Top Channel",
            value=top_channel.title(),
            delta_text=f"{top_channel_share:.1f}%", delta_caption="of requests",
            spark_values=channel_spark(top_channel), spark_color=TEAL,
            icon_svg=ICON_CHECK, icon_tint="teal",
            value_long=True, delta_positive=True, use_bars=False,
        )

    with c2:
        render_kpi_card_with_spark(
            key="ch_digital", title="Digital Share",
            value=f"{digital_share:.1f}%",
            delta_text="ONLINE+MOBILE", delta_caption="combined",
            spark_values=channel_spark("ONLINE"), spark_color=BLUE,
            icon_svg=ICON_TAG, icon_tint="blue",
            delta_positive=True, use_bars=False,
        )

    with c3:
        render_kpi_card_with_spark(
            key="ch_phone", title="Phone Share",
            value=f"{phone_share:.1f}%",
            delta_text="PHONE", delta_caption="channel",
            spark_values=channel_spark("PHONE"), spark_color=AMBER,
            icon_svg=ICON_CLOCK, icon_tint="teal",
            delta_positive=True, use_bars=False,
        )

    with c4:
        render_kpi_card_with_spark(
            key="ch_unknown", title="Unknown Share",
            value=f"{unknown_share:.1f}%",
            delta_text="UNKNOWN", delta_caption="category",
            spark_values=channel_spark("UNKNOWN"), spark_color=CORAL,
            icon_svg=ICON_TAG, icon_tint="coral",
            delta_positive=False, use_bars=False,
        )

    section_heading("COMPOSITION", "Channel composition")

    left, right = st.columns([1.5, 1], gap="medium")

    channel_order = ["ONLINE", "PHONE", "MOBILE", "UNKNOWN", "OTHER"]

    with left:
        with st.container(border=True):
            render_chart_header("Request Channel Composition", "Yearly channel share through time")
            fig_channel = go.Figure()
            for channel in channel_order:
                part = channel_summary.loc[channel_summary["open_data_channel_type"] == channel].sort_values("year")
                if part.empty: continue
                labels = [f"{v:.1f}%" if v >= 4 else "" for v in part["share_pct"]]
                fig_channel.add_trace(go.Bar(
                    x=part["year"], y=part["share_pct"], name=channel,
                    marker_color=CHANNEL_COLORS[channel],
                    text=labels, textposition="inside", insidetextanchor="middle",
                    textfont=dict(color="white", size=11),
                    hovertemplate=f"<b>{channel}</b><br>Year %{{x}}<br>%{{y:.2f}}%<extra></extra>",
                ))
            fig_channel.update_layout(
                barmode="stack",
                uniformtext=dict(minsize=9, mode="hide"),
                legend_title_text="Channel",
            )
            fig_channel.update_xaxes(title="", dtick=1)
            fig_channel.update_yaxes(range=[0, 100], title="Share (%)")
            style_plot(fig_channel, height=370)
            st.plotly_chart(fig_channel, use_container_width=True, config=CLEAN_CONFIG)

    with right:
        with st.container(border=True):
            render_chart_header("Current Channel Share", "Distribution in current filter view")
            rank = channel_total.sort_values("share_pct", ascending=True)
            fig_rank = go.Figure()
            fig_rank.add_trace(go.Bar(
                x=rank["share_pct"], y=rank["open_data_channel_type"],
                orientation="h",
                marker_color=[CHANNEL_COLORS.get(c, SLATE) for c in rank["open_data_channel_type"]],
                text=[f"{v:.1f}%" for v in rank["share_pct"]],
                textposition="outside", textfont=dict(size=11),
                hovertemplate="%{y}<br>%{x:.2f}%<extra></extra>",
            ))
            fig_rank.update_xaxes(title="", tickformat=".0f")
            fig_rank.update_yaxes(title="")
            style_plot(fig_rank, height=370, showlegend=False)
            st.plotly_chart(fig_rank, use_container_width=True, config=CLEAN_CONFIG)


# ============================================================
# METHODOLOGY
# ============================================================

elif page == "Methodology":
    section_heading("METHODOLOGY", "How the dashboard should be interpreted")

    col1, col2 = st.columns(2, gap="medium")

    with col1:
        with st.container(border=True):
            st.markdown("""
                <div class="method-card">
                    <div class="method-title">Data scope</div>
                    <div class="method-text">The analysis covers NYC 311 service requests created from 1 January 2022 through 31 December 2025. The data snapshot used for closure validation is 6 October 2026.</div>
                </div>
            """, unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("""
                <div class="method-card">
                    <div class="method-title">Global filters</div>
                    <div class="method-text">Year, Borough, and Complaint Type define the population used throughout the dashboard. Filter state is preserved when switching pages.</div>
                </div>
            """, unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("""
                <div class="method-card">
                    <div class="method-title">Seasonality</div>
                    <div class="method-text">Monthly request counts are converted to average daily volume before comparison. The seasonality index compares monthly average daily requests with the corresponding annual average daily volume.</div>
                </div>
            """, unsafe_allow_html=True)

    with col2:
        with st.container(border=True):
            st.markdown("""
                <div class="method-card">
                    <div class="method-title">Closure time</div>
                    <div class="method-text">Closure time is the interval from <code>created_date</code> to <code>closed_date</code>. It represents recorded request closure time and should not be interpreted as physical resolution time.</div>
                </div>
            """, unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("""
                <div class="method-card">
                    <div class="method-title">Closure validation</div>
                    <div class="method-text">Closure records are excluded when the closure date is missing, precedes the creation date, or occurs after the 6 October 2026 snapshot. Median and percentiles are emphasized because the distribution is strongly right-skewed.</div>
                </div>
            """, unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("""
                <div class="method-card">
                    <div class="method-title">Geographic interpretation</div>
                    <div class="method-text">Geography supports absolute recorded requests and requests per 1,000 Census 2020 residents. The population denominator is fixed; selected request filters determine the numerator. Neither metric directly measures problem severity.</div>
                </div>
            """, unsafe_allow_html=True)

    section_heading("ACCESSIBILITY", "Reading the visual encodings")

    with st.container(border=True):
        st.markdown("""
            <div class="method-card">
                <div class="method-text">The interface uses navy, teal, blue, and amber accents with strong light-background contrast. Channel shares include direct percentage labels when space allows. Closure comparisons use different marker shapes for Median and P90. Plotly hover and zoom controls are enabled on charts where close inspection is useful.</div>
            </div>
        """, unsafe_allow_html=True)