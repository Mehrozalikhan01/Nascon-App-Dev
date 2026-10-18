"""Prometheus exposition for the Phase II application counters and gauges."""


def render_metrics(ws_connections: int, active_streams: int, dlq_depth: int, qr_scans: int) -> str:
    return (
        "# HELP nascon_active_ws_connections Active WebSocket subscribers.\n"
        "# TYPE nascon_active_ws_connections gauge\n"
        f"nascon_active_ws_connections {max(0, int(ws_connections))}\n"
        "# HELP nascon_active_streams_total Active broadcaster streams.\n"
        "# TYPE nascon_active_streams_total gauge\n"
        f"nascon_active_streams_total {max(0, int(active_streams))}\n"
        "# HELP nascon_stream_bitrate_kbps Current stream bitrate in kbps.\n"
        "# TYPE nascon_stream_bitrate_kbps gauge\n"
        "nascon_stream_bitrate_kbps 0\n"
        "# HELP nascon_dlq_depth Undelivered notification count.\n"
        "# TYPE nascon_dlq_depth gauge\n"
        f"nascon_dlq_depth {max(0, int(dlq_depth))}\n"
        "# HELP nascon_qr_scans_total Number of QR resolver reads.\n"
        "# TYPE nascon_qr_scans_total counter\n"
        f"nascon_qr_scans_total {max(0, int(qr_scans))}\n"
    )
