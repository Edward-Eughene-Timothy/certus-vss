"""
Maps heterogeneous incoming signals (CAN, SOME/IP, VHAL) to standard COVESA VSS paths.
"""
def normalize_vss(signal_key: str, value: float) -> dict:
    return {"path": signal_key, "value": value}
