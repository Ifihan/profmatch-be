import asyncio
import logging
from app.services.mcp_client import server_manager, UniversityClient
from app.services.orchestrator import discover_faculty_url

# Configure logging
logging.basicConfig(level=logging.INFO)

async def test_discovery():
    print("🚀 Testing Discovery Layer")
    print("--------------------------")

    # 1. Start University Server
    await server_manager.start_server(UniversityClient.SERVER_SCRIPT)
    
    # 2. Test Generic URL
    test_uni = "https://www.ttu.edu/"
    test_interest = "Computer Science"
    
    print(f"\n[Test] Input: {test_uni} + Interest: {test_interest}")
    
    try:
        discovered_url = await discover_faculty_url(test_uni, test_interest)
        print(f"✅ Discovered URL: {discovered_url}")
        
        if "ttu.edu" in discovered_url and "cs" in discovered_url.lower():
            print("PASS: Found a likely CS directory.")
        else:
            print("WARNING: Result doesn't look like a CS directory (might be valid though).")
            
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Cleanup
    await server_manager.close_all()

if __name__ == "__main__":
    asyncio.run(test_discovery())
