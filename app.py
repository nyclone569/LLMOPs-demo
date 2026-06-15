import json
import logging
import altair as alt
import pandas as pd
import streamlit as st

from analytics_agent.config import SCHEMA_REGISTRY_PATH, S3_BUCKET
from analytics_agent.registry import load_registry, validate_registry, validate_s3_paths
from analytics_agent.pipeline import run_pipeline

logging.basicConfig(level=logging.INFO, format="%(message)s")


@st.cache_resource
def get_registry():
    registry = load_registry(SCHEMA_REGISTRY_PATH)
    validate_registry(registry)
    validate_s3_paths(registry, bucket=S3_BUCKET)
    return registry


def render_chart(chart_spec: dict, rows: list[dict]) -> None:
    if not chart_spec or not rows:
        return
    df = pd.DataFrame(rows)
    chart_type = chart_spec.get("type")
    x = chart_spec.get("x")
    y = chart_spec.get("y")
    series = chart_spec.get("series") or []

    try:
        if chart_type == "table":
            st.dataframe(df, use_container_width=True)
            return

        base = alt.Chart(df)
        color = (
            alt.Color(f"{series}:N")
            if series and isinstance(series, str) and series in df.columns
            else alt.value("#4C78A8")
        )

        if chart_type == "bar":
            chart = base.mark_bar().encode(x=f"{x}:O", y=f"{y}:Q", color=color)
        elif chart_type == "line":
            chart = base.mark_line(point=True).encode(x=f"{x}:O", y=f"{y}:Q", color=color)
        elif chart_type == "pie":
            # Altair has no native pie — render as horizontal bar
            chart = base.mark_bar().encode(
                y=alt.Y(f"{x}:N", sort="-x"), x=f"{y}:Q", color=color
            )
        else:
            st.warning(f"Unknown chart type: {chart_type}")
            return

        st.altair_chart(chart.properties(width="container"), use_container_width=True)
    except Exception as e:
        st.warning(f"Chart could not be rendered: {e}")


def main():
    st.set_page_config(page_title="NYC Taxi Analytics", layout="wide")
    st.title("NYC Taxi Analytics Agent")
    st.caption("Ask a question about NYC yellow cab trip data.")

    try:
        registry = get_registry()
    except Exception as e:
        st.error(f"Failed to load schema registry or connect to S3: {e}")
        return

    question = st.text_input(
        "Your question", placeholder="e.g. show monthly revenue trend"
    )

    if question:
        with st.spinner("Thinking..."):
            result = run_pipeline(question, registry)

        if result.error:
            st.error(f"Error: {result.error}")
        elif result.clarification:
            st.info(result.clarification)
        elif result.summary:
            st.markdown(result.summary)
            if result.chart_spec and result.rows:
                render_chart(result.chart_spec, result.rows)
            elif result.chart_spec and not result.rows:
                st.caption("No rows available to chart.")
            st.caption(f"Correlation ID: `{result.correlation_id}`")

        with st.expander("Debug log"):
            st.json(result.log)


if __name__ == "__main__":
    main()
