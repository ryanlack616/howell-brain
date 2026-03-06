import httpx, json

BASE = "http://localhost:7777/mcp"

def call_bootstrap(mode):
    r = httpx.post(BASE, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "howell_bootstrap", "arguments": {"mode": mode}}
    }, timeout=30)
    data = r.json()
    if "result" in data:
        content = json.loads(data["result"]["content"][0]["text"])
        size = len(json.dumps(content))
        print(f"  Mode returned: {content.get('mode')}")
        print(f"  Keys: {list(content.keys())}")
        print(f"  Size: {size:,} bytes")
        if "identity" in content:
            print(f"  Identity keys: {list(content['identity'].keys()) if isinstance(content['identity'], dict) else 'string'}")
        if "entity_index" in content:
            print(f"  Entities: {len(content['entity_index'])}")
        if "knowledge_graph" in content:
            kg = content["knowledge_graph"]
            print(f"  KG entities: {kg.get('total_entities')}, relations: {kg.get('total_relations')}")
        if "soul" in content:
            print(f"  Soul length: {len(content['soul'])} chars")
        return size
    else:
        print(f"  Error: {data}")
        return 0

print("=" * 50)
print("TESTING THREE BOOTSTRAP MODES")
print("=" * 50)

print("\n=== CONTINUE ===")
s1 = call_bootstrap("continue")

print("\n=== WARM ===")
s2 = call_bootstrap("warm")

print("\n=== FULL ===")
s3 = call_bootstrap("full")

print("\n=== SUMMARY ===")
print(f"  continue: {s1:>8,} bytes")
print(f"  warm:     {s2:>8,} bytes")
print(f"  full:     {s3:>8,} bytes")
print(f"  ratio:    1 : {s2/max(s1,1):.0f} : {s3/max(s1,1):.0f}")
