"""The look of the monitor: one constant stylesheet, injected once per page run.

This module is the only HTML sink of the UI. ``STYLE`` is a fixed string without runtime data, so
nothing from the database or from Wildberries can ever reach it. Selectors target the stable
``st-key-<key>`` classes of keyed widgets and containers plus a few ``data-testid`` hooks.

The typeface comes from the Streamlit theme (``.streamlit/config.toml``); no rule here sets a
font family, because that would also hit the Material icon font.
"""

from __future__ import annotations

import streamlit as st

STYLE = """<style>
:root {
  --ink: #1F1F29; --muted: #6E6E80; --line: #E7E7EE; --surface: #F6F6F9;
  --primary: #7B10F5; --primary-dark: #4A0E8F; --primary-soft: #F1E6FF;
  --warn-bg: #FFF2D9; --warn-ink: #7A4E00;
  --spring: cubic-bezier(.34,1.56,.64,1);
  --glass-bg: linear-gradient(180deg,#fff,#F7F7FA);
  --glass-shadow: 0 0 0 1px rgba(31,31,41,.06), 0 12px 26px -12px rgba(31,31,41,.45),
    0 3px 8px rgba(31,31,41,.08), inset 0 1px 1px #fff, inset 0 -5px 10px -6px rgba(31,31,41,.12);
  --rim: linear-gradient(110deg,#fff 0%,#D9D9E3 25%,#fff 50%,#D9D9E3 75%,#fff 100%);
}
@keyframes wbmon-sheen { to { background-position: 150% 0; } }
@keyframes wbmon-jelly {
  0% { transform: scale(1,1); }
  30% { transform: scale(1.12,.88); }
  60% { transform: scale(.96,1.04); }
  100% { transform: scale(1,1); }
}
@keyframes wbmon-pulse {
  0% { box-shadow: 0 0 0 0 rgba(31,169,113,.5); }
  70% { box-shadow: 0 0 0 8px rgba(31,169,113,0); }
  100% { box-shadow: 0 0 0 0 rgba(31,169,113,0); }
}

/* browser chrome of Streamlit that a product page does not need */
[data-testid="stToolbar"], [data-testid="stMainMenu"], [data-testid="stAppDeployButton"], footer {
  display: none !important;
}
header[data-testid="stHeader"] { background: transparent; box-shadow: none; pointer-events: none; }
[data-testid="stExpandSidebarButton"] { pointer-events: auto; }

[data-testid="stMainBlockContainer"] { padding-top: 1.25rem; max-width: 1300px; }
[data-testid="stSidebar"] { background: #fff; border-right: 1px solid var(--line); }
h1 { letter-spacing: -.02em; }

/* left menu */
.st-key-nav_label p {
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted);
}
.st-key-section [role="radiogroup"] > label {
  position: relative; width: 100%; margin: 0; padding: 12px 16px; border-radius: 14px;
  color: #3B3B4A;
}
.st-key-section [role="radiogroup"] > label > div:first-child { display: none; }
.st-key-section [role="radiogroup"] > label p { font-size: 14px; font-weight: 600; }
.st-key-section [role="radiogroup"] > label:hover p { color: var(--primary); }
.st-key-section [role="radiogroup"] > label:has(input:checked) {
  position: relative;
  background: var(--glass-bg);
  box-shadow: var(--glass-shadow);
  animation: wbmon-jelly .6s var(--spring);
}
.st-key-section [role="radiogroup"] > label:has(input:checked)::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  pointer-events: none;
  background: var(--rim);
  background-size: 300% 100%;
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  animation: wbmon-sheen 6s linear infinite;
}
.st-key-section [role="radiogroup"] > label:has(input:checked)::after {
  content: "";
  position: absolute;
  left: 14%;
  right: 14%;
  top: 3px;
  height: 40%;
  border-radius: 999px;
  pointer-events: none;
  background: linear-gradient(180deg, rgba(255,255,255,.95), rgba(255,255,255,0));
}
.st-key-section [role="radiogroup"] > label:has(input:checked) p {
  color: var(--primary-dark); font-weight: 700; position: relative; z-index: 1;
}

/* top bar */
.st-key-topbar { border-bottom: 1px solid var(--line); padding-bottom: 10px; margin-bottom: 6px; }
.st-key-topbar strong { font-size: 16px; font-weight: 800; }
[class*="st-key-pill_"] {
  display: inline-flex; flex-direction: row; align-items: center; gap: 8px;
  padding: 7px 12px; border-radius: 999px; background: var(--surface);
  border: 1px solid var(--line);
}
[class*="st-key-pill_"] p { margin: 0; font-size: 12.5px; font-weight: 600; color: var(--muted); }
[class*="st-key-pill_"]::before {
  content: ""; width: 8px; height: 8px; border-radius: 50%; flex: none;
}
.st-key-pill_live::before { background: #1FA971; animation: wbmon-pulse 2s infinite; }
.st-key-pill_stale::before { background: #F2A33A; }
.st-key-pill_none::before { background: #A0A0B0; }
.st-key-pill_demo::before { background: var(--primary); }

/* real / demo switch */
.st-key-mode [data-testid="stButtonGroup"] {
  padding: 4px; border-radius: 999px; background: var(--surface); border: 1px solid var(--line);
  gap: 2px;
}
.st-key-mode button[kind="segmented_control"] {
  border: 0; background: transparent; border-radius: 999px; font-size: 13px; font-weight: 700;
  color: var(--muted);
}
.st-key-mode button[kind="segmented_controlActive"] {
  position: relative;
  background: var(--glass-bg);
  box-shadow: var(--glass-shadow);
  animation: wbmon-jelly .6s var(--spring);
  border: 0; border-radius: 999px; color: var(--primary-dark);
}
.st-key-mode button[kind="segmented_controlActive"]::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  pointer-events: none;
  background: var(--rim);
  background-size: 300% 100%;
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  animation: wbmon-sheen 6s linear infinite;
}

/* period control */
.st-key-period button[kind="segmented_control"] {
  background: var(--surface); border-color: var(--line); font-weight: 600;
}
.st-key-period button[kind="segmented_controlActive"] {
  background: var(--primary-dark); color: #fff; border-color: var(--primary-dark);
}
.st-key-period button[kind="segmented_controlActive"] p { color: #fff; }

/* info cards */
[data-testid="stAlertContainer"] { border-radius: 14px; font-weight: 500; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {
  background: var(--warn-bg); color: var(--warn-ink);
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) p {
  color: var(--warn-ink);
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) {
  background: #FDECEC; color: #8A1C1C;
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) {
  background: var(--surface); color: var(--ink);
}
[data-testid="stAlertDynamicIcon"] {
  width: 22px; height: 22px; border-radius: 50%; color: #fff; display: flex;
  align-items: center; justify-content: center; background: var(--primary);
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"])
  [data-testid="stAlertDynamicIcon"] { background: #F08A24; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"])
  [data-testid="stAlertDynamicIcon"] { background: #EF6B6F; }

/* demo card */
.st-key-demo_card {
  background: var(--primary-soft); color: #4A1A88; border-radius: 14px; padding: 12px 18px;
}
.st-key-demo_card p { color: #4A1A88; }
.st-key-demo_off button {
  background: #fff; border: 1px solid var(--line); border-radius: 12px; font-weight: 700;
}

/* white cards */
[class*="st-key-card_"] {
  border: 1px solid var(--line); border-radius: 18px; padding: 20px 22px; background: #fff;
}

/* empty state */
.st-key-card_empty { text-align: center; padding: 46px 24px; }
.st-key-empty_orb {
  position: relative;
  background: var(--glass-bg);
  box-shadow: var(--glass-shadow);
  width: 82px; height: 82px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-size: 34px; margin: 0 auto 16px;
}
.st-key-empty_orb::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  pointer-events: none;
  background: var(--rim);
  background-size: 300% 100%;
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  animation: wbmon-sheen 6s linear infinite;
}
.st-key-empty_orb p { font-size: 34px; margin: 0; position: relative; z-index: 1; }
.st-key-empty_chips p {
  margin: 0; padding: 8px 12px; border-radius: 999px; background: var(--surface);
  border: 1px solid var(--line); font-size: 12.5px; font-weight: 600;
}

/* KPI tiles */
.st-key-kpis [data-testid="stMetric"] {
  border-radius: 18px; border-color: var(--line); padding: 16px 18px;
}
.st-key-kpis [data-testid="stMetricLabel"] p {
  font-size: 12px; font-weight: 600; color: var(--muted);
}

/* product cards */
[class*="st-key-pcard_"] {
  border: 1px solid var(--line); border-radius: 18px; padding: 10px;
  transition: transform .3s var(--spring), box-shadow .3s;
}
[class*="st-key-pcard_"]:hover {
  transform: translateY(-3px); box-shadow: 0 18px 30px -18px rgba(31,31,41,.35);
}
[class*="st-key-pimg_"] {
  min-height: 96px; border-radius: 12px; padding: 8px;
  background: linear-gradient(135deg,#F3F0FA,#E9E6F2);
}

/* ghost CSV button */
.st-key-filters [data-testid="stDownloadButton"] button {
  background: #fff; color: var(--ink); border: 1px solid var(--line); border-radius: 12px;
  font-weight: 700;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>"""


def inject() -> None:
    """Put the stylesheet on the page (the single call that renders HTML in the UI)."""
    st.html(STYLE)
