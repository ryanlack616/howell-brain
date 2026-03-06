import json
from datetime import datetime
from pathlib import Path

# Create consolidation record
consol_file = Path("C:/home/howell-persist/bridge/last_consolidated.json")
consol_file.write_text(json.dumps({
    "timestamp": datetime.now().isoformat(),
    "note": "KG synced from MCP memory, 29->49 entities. Bootstrap three-mode system deployed."
}, indent=2), encoding="utf-8")
print(f"Consolidation recorded: {consol_file}")

# Verify heartbeat now passes
from howell_bridge import run_heartbeat
report = run_heartbeat()
print(f"Status: {report.get('status', 'unknown')}")
print(f"Report type: {type(report)}")
print(f"Report keys: {list(report.keys()) if isinstance(report, dict) else report}")
issues = report.get("issues", [])
if issues:
    for i in issues:
        print(f"  Issue: {i}")
else:
    print("  No issues!")
