# TripMate-Multiagent-Travel-Planner# Modular Multi-Agent Travel Planner

A modular, stateful AI travel-planning system built around **LangGraph**, with dynamic supervisor routing, specialist agents, external tool integrations through **MCP and custom tools**, PostgreSQL-backed workflow persistence, and human-in-the-loop approval.

The system is designed as an orchestration workflow rather than a single LLM call. A user request is first evaluated by an input guardrail, then analyzed by a supervisor that determines which specialist agents are required. The selected agents collect and analyze information before an itinerary is generated and presented for human approval.

## Overview

The project demonstrates how multiple specialized AI agents can cooperate inside a controlled workflow while maintaining shared state across the execution.

Instead of sending every request through every available agent, the supervisor dynamically selects the required capabilities based on the user's request.

Available specialist agents include:

* **Flight Agent** - analyzes flight routes, airports, airlines, duration, and airfare guidance.
* **Hotel Agent** - retrieves accommodation and destination information.
* **Weather Agent** - retrieves current weather and forecast information.
* **Budget Agent** - evaluates estimated trip costs, budget risks, and feasibility.
* **Itinerary Agent** - combines the available information into an integrated travel plan.

The workflow also includes:

* Input guardrails
* Dynamic agent routing
* MCP-based external tool integration
* Custom tool integration
* Shared workflow state
* PostgreSQL checkpointing
* Human-in-the-loop approval
* Revision and resume support
* Intermediate result serialization

## Architecture

```text
                         User Request
                              │
                              ▼
                     ┌─────────────────┐
                     │ Input Guardrail │
                     └────────┬────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                 Blocked              Allowed
                    │                   │
                    ▼                   ▼
                 Response         ┌────────────┐
                                  │ Supervisor │
                                  └─────┬──────┘
                                        │
                           Dynamic Agent Selection
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
                    ▼                   ▼                   ▼
               Flight Agent       Hotel Agent        Weather Agent
                    │                   │                   │
                    └───────────────────┼───────────────────┘
                                        │
                                        ▼
                                  Budget Agent
                                        │
                                        ▼
                                Itinerary Agent
                                        │
                                        ▼
                               Human Approval
                                  │       │
                              Approved   Revision
                                  │       │
                                  └───┬───┘
                                      ▼
                                Final Response
```

The workflow is implemented as a LangGraph `StateGraph`. The supervisor determines the required execution path, while the itinerary agent acts as the integration point for the selected specialist results.

## Core Design

### 1. Supervisor-Based Routing

The supervisor analyzes the user's request and produces:

* Selected specialist agents
* Extracted trip constraints
* Routing reasoning
* Guardrail status

The system then executes only the selected specialists while ensuring that the itinerary agent is included as the final planning stage.

This allows the workflow to adapt to different requests.

For example:

```text
"What will the weather be like in Tokyo?"
        │
        ▼
Supervisor
        │
        └── Weather Agent
                │
                ▼
          Itinerary Agent
```

while a more complex request could produce:

```text
"Plan a 7-day trip from Tehran to Paris under $2,000."

Supervisor
    │
    ├── Flight Agent
    ├── Hotel Agent
    ├── Weather Agent
    ├── Budget Agent
    └── Itinerary Agent
```

The goal is to avoid treating every travel request as requiring the same fixed execution path.

## 2. MCP and Custom Tool Integration

The project supports two approaches to connecting agents with external capabilities:

### MCP

External capabilities can be accessed through MCP-based integrations.

Examples include:

* Flight and aviation information
* Hotel/search information
* Weather data
* Forecast data

The MCP layer keeps external service interaction separated from the agent orchestration layer.

### Custom Tools

The architecture also supports direct/custom tool implementations.

This allows the same agent-oriented workflow to work with either MCP-backed capabilities or application-specific tools without requiring the entire orchestration layer to be redesigned.

This separation is useful when experimenting with different tool protocols, providers, or internal services.

## 3. Shared State

Agents communicate through a shared workflow state.

The state contains information such as:

```text
User query
Trip constraints
Selected agents
Supervisor reasoning
Flight results
Hotel results
Weather results
Budget analysis
Draft itinerary
Human approval
Human feedback
Final response
Model call count
```

This allows downstream agents to consume the results produced by previous stages instead of independently reconstructing the user's request.

## 4. Human-in-the-Loop

The itinerary is not immediately treated as the final answer.

After generating a draft itinerary, the workflow pauses and requests human review.

The reviewer can:

* Approve the itinerary
* Reject it
* Provide revision feedback

The workflow can then resume from the persisted execution state.

Conceptually:

```text
Generate Draft
      │
      ▼
Human Review
   │       │
   │       └── Feedback
   │              │
   ▼              ▼
Approve       Revise Draft
   │              │
   └──────┬───────┘
          ▼
    Final Response
```

This provides a controlled point where a human can intervene before the final travel plan is generated.

## 5. Persistent Workflow State

The project uses PostgreSQL as the LangGraph checkpointer.

Each execution is associated with a `thread_id`, allowing an interrupted workflow to be resumed later.

This is particularly important for human-in-the-loop workflows because the application does not need to keep the entire execution only in process memory.

The workflow can:

1. Start a travel-planning request.
2. Execute the required agents.
3. Generate a draft itinerary.
4. Pause for human review.
5. Persist the execution state.
6. Resume using the same thread.
7. Generate the final response.

## Agent Responsibilities

| Agent           | Responsibility                                                   |
| --------------- | ---------------------------------------------------------------- |
| Supervisor      | Guardrail evaluation, constraint extraction, and dynamic routing |
| Flight Agent    | Flight and aviation information                                  |
| Hotel Agent     | Accommodation and destination search                             |
| Weather Agent   | Current weather and forecasts                                    |
| Budget Agent    | Cost estimation and budget feasibility                           |
| Itinerary Agent | Integrates specialist results into a travel plan                 |
| Human Approval  | Reviews and approves or requests revisions                       |
| Final Agent     | Produces the final user-facing response                          |

## Technology Stack

* Python
* LangGraph
* LangChain
* Groq / Llama 3.3 70B
* MCP
* PostgreSQL
* Psycopg
* External travel and search APIs/tools
* Environment-based configuration

## Model

The current implementation uses:

```text
Llama 3.3 70B Versatile
```

through Groq.

The model is used for tasks including:

* Guardrail classification
* Supervisor planning
* Specialist reasoning
* Budget analysis
* Itinerary generation
* Final response generation

External information retrieval is handled separately through tool integrations rather than relying exclusively on the language model's internal knowledge.

## Configuration

The application expects environment variables for external services and persistence.

Example:

```env
GROQ_API_KEY=your_groq_api_key
DATABASE_URL=your_postgresql_connection_string
```

The application validates the required credentials during startup.

## Example Workflow

Example user request:

```text
I want to spend 7 days in Japan from Tehran.
My budget is $2500 and I prefer cultural activities.
```

The workflow can perform the following sequence:

```text
User Request
     │
     ▼
Guardrail
     │
     ▼
Supervisor
     │
     ├── Flight Agent
     ├── Hotel Agent
     ├── Weather Agent
     ├── Budget Agent
     └── Itinerary Agent
             │
             ▼
       Draft Itinerary
             │
             ▼
       Human Approval
             │
             ▼
       Final Response
```

The exact execution path depends on the supervisor's selected agents.

## Project Goals

This project is primarily an engineering exploration of modular agent orchestration.

The main goals are:

* Designing multi-agent workflows with explicit responsibilities
* Experimenting with dynamic routing
* Separating reasoning from external tool execution
* Comparing MCP-based and custom tool integrations
* Maintaining persistent agent state
* Implementing human-controlled workflow checkpoints
* Building modular components that can be replaced independently

## Engineering Considerations

The system intentionally separates several concerns:

```text
Orchestration
      │
      ├── Agent Selection
      ├── Workflow State
      └── Execution Control

Agent Logic
      │
      ├── Flight
      ├── Hotel
      ├── Weather
      ├── Budget
      └── Itinerary

Tool Layer
      │
      ├── MCP
      └── Custom Tools

Persistence
      │
      └── PostgreSQL / LangGraph Checkpointing
```

This makes it possible to change a tool provider or specialist implementation without redesigning the entire workflow.

## Current Limitations

This project is an engineering and architecture project rather than a claim of production-scale autonomous travel booking.

Current limitations include:

* External APIs may not always provide live pricing.
* Search and weather providers can fail or become unavailable.
* LLM-generated estimates should not be treated as authoritative prices.
* The system does not automatically purchase tickets or hotel reservations.
* Tool availability depends on the configured providers.
* The current implementation requires further testing and hardening before production deployment.

## Future Improvements

Potential extensions include:

* More robust structured-output validation
* Agent-level evaluation and tracing
* Better retry and failure policies
* Parallel specialist execution where dependencies allow it
* Tool-selection benchmarking
* MCP versus custom-tool performance comparison
* More granular agent observability
* Cost and latency tracking
* Automated workflow evaluation
* Additional travel-domain specialists
* Production API and authentication layer
* More sophisticated itinerary revision loops

## Project Status

This project is under active development.

The current implementation focuses on the architecture and engineering patterns required for a modular multi-agent system rather than simply producing a conversational travel chatbot.
