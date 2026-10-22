# CertusVSS: Deterministic On-Device Vehicle Telemetry Intelligence

A deterministic, privacy-first architecture for in-vehicle natural language diagnostics under embedded edge constraints (~2 GB RAM, 2x ARM Cortex-A cores).

Developed for **Tata Elxsi TELIPORT Season 4 (Case Study 5)** by **Team Wired Caterva**:
- Edward Eughene Timothy (Team Lead & Systems Architect)
- Aryan A Sajith (Automotive Ingestion & Protocols Engineer)
- Bhavik Kumar M (HMI, Verification & Presentation Lead)

## Core Philosophy
**"SQL Computes, SLM Explains"** — Complete arithmetic isolation where embedded SQLite computes verified metrics in <15ms, and an Arm-optimized local SLM summarizes findings without mathematical hallucinations.
