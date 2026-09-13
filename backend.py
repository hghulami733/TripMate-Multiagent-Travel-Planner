import os 
import certifi
from dotenv import load_dotenv 
from typing import TypedDict, Annotated, Any
import operator
import uuid
import psycopg
from psycopg.row import dict_row
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import (
    AnyMessage,
    HumanMessage, 
    AIMessage,
    SystemMessage
)
from langchain_groq import ChatGroq
#from tools.tavily_tool import tavily_search
#from tools.flight_tool import search_flights
import asyncio
# using MCP for Tavily
from mcp_client import (
    tavily_mcp_search, aviation_stack_mcp_call,
    extract_destination, forecast_mcp_search, 
    weather_mcp_search,

)
import json

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")

#Model 
model = ChatGroq(
    model = "llama-3.3-70b-versatile",
    api_key=GROQ_API_KEY
)

class TravelState(TypedDict, total=False):
    messages : Annotated[list[AnyMessage], operator.add]
    user_query: str

    # Supervisor + guardrail state
    guardrail_allowed = bool
    guardrail_reason: str
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str

    # Original specialist results
    flight_results: str
    hotel_results: str
    itinerary: str
    model_calls: int
    weather_results = str

    # New budget + HTML state
    budget_results : str
    approval_request: str
    approved: bool
    human_feedback: str
    final_response: str

KNOWN_AGENTS = {
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent"
}

AGENT_ORDER = [
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent"
]

def _model_text(system_prompr: str, user_prompr: str) -> str:
    response = model.invoke(
        [
            SystemMessage(content=system_prompr),
            HumanMessage(content=user_prompr)
        ]
    )
    return str(response.content)

def _json_from_model(text: str) -> dict[str, Any]:
    """ Extract the first complete JSON object returend by the model."""
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError("The model did not return a JSON object.")

    return json.loads(text[start : end + 1])

def _empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "budget": "",
        "travel_style": "",
        "special_preferences": [],
    }

# Supervisor Agent + Input Guardrail
def supervisor_agent(state: TravelState):
    query = state["user_query"]
    model_calls = state.get("model_calls", 0)

    guardrail_prompt = f"""

    Determine whether the following request belongs to travel planning or travel
    information. Valid requests can include destinations, flights, hotels, weather,
    budgets, visas, transportation, sightseeing, food, packing, or itineraries.

    Block clearly unrelated requests and requests asking for harmful or illegal
    instructions. Do not block a valid travel request merely because some details
    are missing.

    Return strict JSON only:

    "allowed": true,
    "reason": ""

    User request:
    {query}
    """

    # Fail open on parser/model errors so a temporary JSON-format issue does not
    # break the original travel-planning behavior.
    try:
        guardrail_raw = _model_text(
            "You are the input guradrail for a travel-planner application."
            "Return strict JSON only.",
            guardrail_prompt,
        )
        guardrail_result = _json_from_model(guardrail_raw)
        allowed = bool(guardrail_result.get("allowed", True))
        guardrail_reason = str(guardrail_result.get("reason", "")).strip()
        model_calls +=1

    except Exception as exc:
        print(f"Guardrail fallback used: {exc}")
        allowed = True
        guardrail_reason = "Guardrail validation fallback allowed the request."

    if not allowed:
        reason = guardrail_reason or (
            "TripMate AI can only help with travel-planner requests. "
            "Please ask about a destination, flight, hotel, weatehr, budget, "
            "or itinerary"
        )

        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [AIMessage(content=f"Guardrail blocked request: {reason}")],
            "model_calls": model_calls,
        }

    supervisor_prompt = f"""
        You are the supervisor of a multi-agent travel-planning system.
        Choose only the specialist agents needed for the request.

        Available agents:
        - flight_agent: flights, airports, airlines, routes, airfare, or booking advice
        - hotel_agent: hotels, accommodation, neighborhoods, or places to stay
        - weather_agent: weather, climate, season, forecast, or packing advice
        - budget_agent: cost, affordability, price limits, or budget feasibility
        - itinerary_agent: creates the integrated travel plan and must always be included

        Return strict JSON only using this schema:

        {{
        "selected_agents": ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"],
        "trip_constraints": {{
        "destination": "",
        "origin": ""
        "duration":
        "budget": ""
        "travel_style": "",
        "special_preferences": []
        }},
        "reasoning: ""
    }}

    User request:
    {query}
    """

    try:
        supervisor_raw = _model_text(
            "You Route work to travel specialist agents. Return strict JSON only.",
            supervisor_prompt,
        )
        parsed = _json_from_model(supervisor_raw)
        requested_agents = parsed.get("selected_agents", [])
        selected_agents = [
            name for name in AGENT_ORDER
            if name in requested_agents and name in KNOWN_AGENTS
        ]

        # The itinerary agent integrates whichever speciallist results were selected.
        if "itinerary_agent" not in selected_agents:
            selected_agents.append("itinerary_agent")

        constraints = _empty_constraints()
        parsed_constraints = parsed.get("trip_constraints", {})
        if isinstance(parsed_constraints, dict):
            constraints.update(parsed_constraints)

        reasoning = str(parsed.get("reasoning", "")).strip()
        model_calls += 1

    except Exception as exe:
        print(f"Supervisor fallback used: {exc}")
        #Original workflow behavior is preserved as the fallback.
        selected_agents = AGENT_ORDER.copy()
        constraints = _empty_constraints()
        reasoning = (
            "Supervisore parsing failled, so the original full travel workflow"
            "was selected as a safe fallback"
        )

    return {
        "guardrail_allowed": True,
        "guardrail_reason": guardrail_reason,
        "selected_agents": selected_agents,
        "trip_constraints": constraints,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "model_calls": model_calls,
            }

# Guardrail blocked response
def guardrail_blocked_agent(state: TravelState):
    reason = state.get("final_response") or state.get("guardrail_reason") or (
        "This request was blocked bt the travel input guardrail"
    )
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason)],
    }

def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. Please add your Render PostgreSQL External Database URL to .env"
        )

    if "sslmode=" not in database_url:
        seperator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{seperator}sslmode=require"

    return database_url

# Flight agent
# def flight_agent(state: TravelState):
#     query = state["user_query"]
#     flight_data = search_flights(query)

#     return {
#         "flight_results": flight_data,
#         "messages": [
#             AIMessage(content="Flight results fetched")
#         ],
#         "model_calls": state.get("model_calls", 0) + 1
#     }

# Flight Tool Router Prompt
FLIGHT_AGENT_PROMPT = """
    You are a travel flight expert.

    User Query:
    {query}

    Airport Information:
    {airport_data}

    Airline Information:
    {airline_data}

    Generate:

    1. Likely departure airport
    2. Likely arrival airport
    3. Airlines serving this route
    4. Typical flight duration
    5. Estimated airfare range
    6. Peak season pricing warning
    7. Booking advice

    Return concise travel guidance.

"""

# Flight Agent
def flight_agent(state: TravelState):
    print("\nINSIDE FLIGHT AGENT\n")

    query = state["user_query"]

    try:
        airports = asyncio.run(
            aviation_stack_mcp_call(
                "list_airports"
            )
        )

        airlines = asyncio.run(
            aviation_stack_mcp_call(
                "list_airlines"
            )
        )

        print("\nAIRPORTS:", airports)
        print("\nAIRLINES:", airlines)

        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            airport_data=str(airports)[:3000],
            airline_data = str(airlines)[:3000]
        )

        response = model.invoke([
            SystemMessage(
                content="You are an expert travel flight planner."
            ),
            HumanMessage(content=prompt)
        ])

        flight_data =response.content

    except Exception as e:
        flight_data = f"Flight information uvavailable: {str(e)}"

    return {
        "flight_results": flight_data,
        "messages": [
            AIMessage(
                content="Flight recommendations generated"
            )
        ],
        "model_calls": state.get("model_calls", 0) + 1
    }

# Hotel agent
def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['user_query']}"

    try:
        #hotel_results = tavily_search(query)
        hotel_results = asyncio.run(tavily_mcp_search(query))

    except Exception as e:
        print(
            f"HOTEL AGENT MCP ERROR: "
            f"{type(e).__name__}: {e}",
            flush=True
        )

        hotel_results = (
            "Live hotel search is temporarily unavailable. "
            "Provide general accommodation and neighborhood "
            "guidance based on the destination and clearly "
            "label it as non-live advice."
        )

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(
                content="Hotel information processed."
            )
        ],
        "model_calls": state.get("model_calls", 0) + 1
    }

# Weather agent
def weather_agent(state: TravelState):
    city = extract_destination(state["user_query"])

    try:
        weather_data = asyncio.run(
            weather_mcp_search(city)
        )

        forecast_data = asyncio.run(
            forecast_mcp_search(city)
        )

        weather_results = f"""
                Current Weather:
                {weather_data}

                Forecast:
                {forecast_data}
            """

    except Exception as e:
        print(
            f"WEATHER AGENT MCP ERROR: "
            f"{type(e) .__name__}: {e}",
            flush=True,
        )

        weather_results = (
            f"Live weather information for {city} "
            "is temporarily unavailable. Give general "
            "seasonal guidance and advise the traveler "
            "to verify the forecast before departure."
        )

    return {
        "weather_results": weather_results,
        "messages": [
            AIMessage(
                content="Weather information processes"
            )
        ]
    }

# Budget Agent
def budget_agent(state:TravelState):
    prompt = f"""

    Analyze whether this trip is realistic for the user's budget.

    User Query:
    {state['user_query']}

    Trip Constraints:
    {state.get('trip_constraints', {})}

    Flight Results:
    {state.get('flight_results', '')}

    Hotel Results:
    {state.get('hotel_results','')}

    Weather Results:
    {state.get('weather_results', '')}

    Return:
    1. Estimated cost categories
    2. Budget risk areas
    3. Money-saving suggestions
    4. Overall feasibility

    If exact live prices are unavailable, clearly label estimates as approximate.
    """

    response = model.invoke(
        [
            SystemMessage(content="You are a practical travel budget analyst."),
            HumanMessage(content=prompt)
        ]
    )

    return {
        "budget_results": response.content,
        "messages": [AIMessage(content="Budget assessment generated.")],
        "model_calls": state.get("model_calls", 0) + 1
    }

# Itinerary agent
def itinerary_agent(state: TravelState):
    prompt = f"""
        Create a complete travel itinerary.

        User Query:
        {state['user_query']}

        Trip Constraints:
        {state['trip_constraints', {}]}

        Flight Results:
        {state['flight_results', '']}

        Hotel Results:
        {state['hotel_results', '']}

        Weather Results:
        {state['weather_results', '']}

        Budget Results:
        {state['budget_results', '']}

        Make the itinerary practical, budget-aware, and easy to follow.
    """

    response = model.invoke([
        SystemMessage(content="You are an expert travel planner."),
        HumanMessage(content=prompt)
    ])

    approval_request = (
        "Please review the generated draft itinerary. Approve it to create the"
        "final polished plan, or provide feedback for revision"
    )

    return {
        "itinerary": response.content,
        "approval_request": approval_request,
        "messages": [AIMessage(content="Draft itinerary created for human review.")],
        "model_calls": state.get("model_calls", 0) + 1
    }

# Human in the loop approval
def human_approval_agent(state:TravelState):
    review = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "expected_response": {
            "approved": True,
            "feedback": "Optional revision feedback",
            }
        }
    )

    approved = bool(review.get("approved", False))
    human_feedback = str(review.get("feedback", "")).strip()

    return {
        "approved": approved,
        "human_feedback": human_feedback,
        "messages": [AIMessage(content="Human approval step completed.")],
    }

# Final Response Agent
def final_agent(state: TravelState):
    if state.get("approved", False):
        review_instruction = (
            "The user approved the draft. Preserve its decisions while polishing it."
        )

    else:
        review_instruction = f"""

        The user requested a revision. Apply this feedback carefully:
        {state.get('human_feedback', '') or 'Improve the draft before finalizing it.'}
        """
    final_prompt = f"""
        Generate the final travel response for the user.

        Human Review:
        {review_instruction}

        User Request:
        {state['user_query']}

        Supervisor Constraints:
        {state.get('trip_constraints', {})}

        Flights:
        {state.get('flight_results', '')}

        Hotels:
        {state.get('hotel_results', '')}

        Weather:
        {state.get('weather_results', '')}

        Budget Analysis:
        {state.get('budget_results', '')}

        Draft Itinerary:
        {state.get('itinerary', '')}

        Format the final answer beautifully using these sections:

        1. Trip Summary
        2. Flight Information
        3. Hotel Suggestions
        4. Weather Information
        5. Day-by-Day Itinerary
        6. Estimated Budget
        7. Final Recommendations

        Important:
        - Be clear and practical.
        - Mention that live flight API may not provide ticket prices if pricing is unavailable
        - Include weather-based travel advice.
        - Keep the response useful for real travel planning.
        - Incorporate the human feedback when revision was requested.
    """

    response = model.invoke([
        SystemMessage(content="You are a professional AI travel booking assistant."),
        HumanMessage(content=final_prompt)
    ])

    return {
        "final_response": response.content,
        "messages": [response],
        "model_calls": state.get("model_calls", 0) + 1
    }

# Dynamic Supervisor Routing
ROUTE_MAP = {
"guardrail_blocked": "guardrail_blocked",
"flight_agent": "flight_agent",
"hotel_agent": "hotel_agent",
"weather_agent": "weather_agent",
"budget_agent": "budget_agent",
"itinerary_agent": "itinerary_agent",
}

def _selected_agents(state:TravelState) -> list[str]:
    selected = state.get("selected_agents", [])
    return [agent for agent in AGENT_ORDER if agent in selected]

def route_from_supervisor(state: TravelState) -> str:
    if not state.get("guardrail_allowed", True):
        return "Guardrail_blocked"

    selected = _selected_agents(state)
    return selected[0] if selected else "itinerary_agent"

def route_after_agent(current_agent: str):
    def route(state:TravelState) -> str:
        selected = _selected_agents(state)
        current_index = AGENT_ORDER.index(current_agent)

        for next_agent in AGENT_ORDER[current_index + 1:]:
            if next_agent in selected:
                return next_agent

        return "itinerary_agent"

    return route



# Graph
graph = StateGraph(TravelState)

graph.add_node("supervisor", supervisor_agent)
graph.add_node("guardrail_blocked", guardrail_blocked_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("budget_agent", budget_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("human_approval", human_approval_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)
graph.add_conditional_edges("flight_agent", route_after_agent("flight_agent"), ROUTE_MAP)
graph.add_conditional_edges("hotel_agent", route_after_agent("hotel_agent"), ROUTE_MAP)
graph.add_conditional_edges("weather_agent", route_after_agent("weather_agent"), ROUTE_MAP)
graph.add_conditional_edges("budget_agent", route_after_agent("budget_agent"), ROUTE_MAP)

graph.add_edge("itinerary_agent", "human_approval")
graph.add_edge("human_approval", "final_agent")
graph.add_edge("final_agent", END)
graph.add_edge("guardrail_blocked", END)

# PostgreSQL checkpointer
DATABASE_URL = get_database_url()

_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row
)

checkpointer = PostgresSaver(_conn)
checkpointer.setup()

travel_workflow = graph.compile(checkpointer=checkpointer)

# FastAPI
def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}

def _serialize_result(
        result: dict[str, Any],
        thread_id: str,
) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = messages[-1].content if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = interrupt_payload.get("draft_itinerary") or result.get(
            "itinerary", ""
        )

    return {
        "thread_id": thread_id,
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": (
        interrupt_payload.get("approval_request", "")
        if interrupt_payload
        else result.get("approval_request", "")
        ),

        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "budget_results": result.get("budget_results", ""),
        "itinerary": (
            interrupt_payload.get("draft_itinerary", "")
            if interrupt_payload
            else result.get("itinerary", "")
        ),
        
        "selected_agents": result.get("selected_agents", []),
        "trip_constraints": result.get("trip_constraints", {}),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason",""),
        "approved": result.get("approved"),
        "human_feedback": result.get("human_feedback", ""),
        "11m_calls": result.get("11m_calls", 0),
    }


def run_travel_agent(user_input: str, thread_id: str | None = None):
    """ Start a new travel-planning run and pause at huamn approval."""
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {
        "configurable":{
            "thread_id": thread_id
        }
    } 

    results = travel_workflow.invoke(
        {
            "messages": [
                HumanMessage(content=user_input)
            ],
            "user_query": user_input,
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": "",
            "flight_results": "",
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "itinerary": "",
            "approval_request": "",
            "approved": False,
            "human_feedback": "",
            "final_response": "",
            "model_calls": 0
        },
        config=config
    )

    return _serialize_result(results, thread_id)

def resume_travel_agent(
        thread_id: str,
        approved: bool,
        feedback: str= "",
):
    """Resume the paused LangGraph thread after human review."""
    if not thread_id:
        raise ValueError("thread_id is required to resume a travel plan.")

    config = {"configurable": {"thread_id": thread_id}}
    result = travel_workflow.invoke(
        Command(
            resume = {
                "approved": approved,
                "feedback": feedback.strip()
            }
        ),
        config = config,
    )

    return _serialize_result(result, thread_id)