# from tools.tavily_tool import tavily_search
# from tools.flight_tool import search_flights

# #response = tavily_search("Best hotels in Germany")
# response = search_flights("plan a 7 days Japan trip from Germany")
# print(response)

import asyncio 
from mcp_client_text import get_all_tools, tavily_mcp_search

if __name__ == "__main__":
    query = "What is ML?"
    asyncio.run(tavily_mcp_search(query))