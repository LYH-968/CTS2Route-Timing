#!/usr/bin/env python3
"""Plot separate M0 training curves from training_log.jsonl."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


RUN = Path(r"E:\CTS\outputs\cts_route_m0_pool_20260922")
OUT = Path(r"E:\CTS\experiments\cts_to_route\M0_pool\results\training_curves")
LOG = RUN / "training_log.jsonl"


def load_rows() -> pd.DataFrame:
    rows = []
    with LOG.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                by_record = row.get("val_by_record", {}).get("model", {})
                per_design = row.get("val_model", {}).get("all", {})
                rows.append({
                    "epoch": int(row["epoch"]),
                    "train_loss": row.get("train_loss"),
                    "val_loss": row.get("val_loss"),
                    "train_mae_ns": row.get("train_mae"),
                    "val_mae_by_record_ns": by_record.get("mae"),
                    "val_mae_per_design_ns": per_design.get("mae"),
                    "train_r2": row.get("train_r2"),
                    "val_r2_by_record": by_record.get("r2"),
                    "val_r2_per_design": per_design.get("r2"),
                    "val_r2_varw_by_record": by_record.get("r2_varw"),
                    "val_r2_varw_per_design": per_design.get("r2_varw"),
                })
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
    return pd.DataFrame(rows).sort_values("epoch").drop_duplicates("epoch")


def base_plot(df: pd.DataFrame, title: str, ylabel: str, filename: str, columns: list[tuple[str, str, str]]) -> None:
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for col, label, color in columns:
        if col in df and df[col].notna().any():
            ax.plot(df["epoch"], df[col], marker="o", markersize=3, linewidth=1.6, label=label, color=color)
    best_row = df.loc[df["val_mae_by_record_ns"].idxmin()]
    last_epoch = int(df["epoch"].max())
    ax.axvline(best_row["epoch"], color="#2ca02c", linestyle="--", linewidth=1.0, alpha=0.8,
               label=f"best val MAE: epoch {int(best_row['epoch'])}")
    ax.axvline(last_epoch, color="#d62728", linestyle=":", linewidth=1.0, alpha=0.8,
               label=f"last completed: epoch {last_epoch}")
    ax.set_title(title)
    ax.set_xlabel("Epoch")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_positive_r2(df: pd.DataFrame) -> None:
    """Plot only positive R2 values; non-positive values are omitted from lines."""
    fig, ax = plt.subplots(figsize=(10, 5.5))
    columns = [
        ("train_r2", "train R2", "#1f77b4"),
        ("val_r2_by_record", "validation R2, per-record macro average", "#ff7f0e"),
        ("val_r2_per_design", "validation R2, per-design", "#9467bd"),
    ]
    for col, label, color in columns:
        values = df[col].where(df[col] > 0)
        if values.notna().any():
            ax.plot(
                df["epoch"],
                values,
                marker="o",
                markersize=3,
                linewidth=1.6,
                label=label,
                color=color,
            )
    best_row = df.loc[df["val_mae_by_record_ns"].idxmin()]
    last_epoch = int(df["epoch"].max())
    ax.axvline(
        best_row["epoch"],
        color="#2ca02c",
        linestyle="--",
        linewidth=1.0,
        alpha=0.8,
        label=f"best val MAE: epoch {int(best_row['epoch'])}",
    )
    ax.axvline(
        last_epoch,
        color="#d62728",
        linestyle=":",
        linewidth=1.0,
        alpha=0.8,
        label=f"last completed: epoch {last_epoch}",
    )
    ax.axhline(0, color="#666666", linewidth=0.8, alpha=0.7)
    ax.set_ylim(bottom=0)
    ax.set_title("M0 positive R2 curves")
    ax.set_xlabel("Epoch")
    ax.set_ylabel("R2 (only values > 0)")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "r2_vs_epoch.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = load_rows()
    df.to_csv(OUT / "training_curves.csv", index=False)
    base_plot(
        df, "M0 loss curves", "Loss",
        "loss_vs_epoch.png",
        [("train_loss", "train loss", "#1f77b4"), ("val_loss", "validation loss", "#ff7f0e")],
    )
    base_plot(
        df, "M0 MAE curves", "MAE (ns)",
        "mae_vs_epoch.png",
        [("train_mae_ns", "train MAE", "#1f77b4"),
         ("val_mae_by_record_ns", "validation MAE, per-record macro average", "#ff7f0e"),
         ("val_mae_per_design_ns", "validation MAE, per-design", "#9467bd")],
    )
    plot_positive_r2(df)
    base_plot(
        df, "M0 variance-weighted R2 curves", "R2_varw",
        "r2_varw_vs_epoch.png",
        [("val_r2_varw_by_record", "validation R2_varw, per-record", "#ff7f0e"),
         ("val_r2_varw_per_design", "validation R2_varw, per-design", "#9467bd")],
    )
    print(df.tail(3).to_string(index=False))
    print(f"saved_to={OUT}")


if __name__ == "__main__":
    main()
