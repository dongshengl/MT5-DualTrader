# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-07

### Added
- Dual-mode strategy engine (`trader_dual.py`): ADX-based market regime detection
  automatically switches between trend-following (顺势) and range/reversal (震荡) modes;
  silent zone (ADX 20~25) opens no new positions
- RSI momentum confirmation with overbought/oversold filters and deviation filter
- Background trading service (`service.py`): runs independently of GUI / desktop
  session (keeps trading after RDP disconnect), JSON-over-TCP control protocol
  on `localhost:19527`
- Tkinter GUI (`gui_dual.py`): parameter editing, start/stop, live status and logs,
  emergency close-all
- PyInstaller spec files for building standalone Windows executables
- Configuration via `config.json` with sensible defaults (see `config.example.json`)

### Notes
- Open-source release of the author's internal MT5自动交易 v3 (June 2026).
- Free forever, no activation password — the legacy binding-password mechanism
  was removed in this release.
