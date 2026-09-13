from pathlib import Path
from typing import Any
from mcp.server.fastmcp import FastMCP
import requests
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

REQUEST_TIMEOUT_SECONDS = 20

mcp = FastMCP("Weather MCP Server")

def _get_api_key() -> str:
    if not OPENWEATHER_API_KEY:
        raise RuntimeError(
            "OPENWEATHER_API_KEY is missing"
            "from the project .env file."
        )
    return OPENWEATHER_API_KEY

def _request_json(
        url: str,
        params: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as e:
        details = ""

        failed_response = getattr(
            e,
            "response",
            None,
        )

        if failed_response is not None:
            details = (
                f" Response: "
                f"{failed_response.text[:500]}"
            )

        raise RuntimeError(
            f"OpenWeather request failed: "
            f"{e}.{details}"
        ) from e

@mcp.tool()
def get_current_weather(city: str) -> dict[str, Any]:
    """ Return the current weather for a city."""
    city = city.strip()

    if not city:
        raise ValueError(
            "city cannot be empty"
        )

    data = _request_json(
        "https://api.openweathermap.org/data/2.5/weather",
        {
            "q": city,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric"
        }
    )

    return {
        "city": data["name"],
        "temperature_c": data["main"]["temp"],
        "feels_like_c": data["main"]["feels_like"],
        "humidity": data["main"]["humidity"],
        "condition": data["weather"][0]["description"],
        "wind_speed": data["wind"]["speed"]
    }

@mcp.tool()
def get_forecast(city: str) -> dict[str, Any]:
    """ Retrun the first five three-hour
    forecast entries for a city.
    """

    city = city.strip()

    if not city:
        raise ValueError(
            "city cannot be empty"
        )
    
    data = _request_json(
        "https://api.openweathermap.org/data/2.5/forecast",

        {
        "q": city,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric"
        }
    )

    forecast = [
            {
                "datetime": item["dt_txt"],
                "temperature": item["main"]["temp"],
                "weather": item["weather"][0]["description"]
            }
            for item in data.get("list", [])[:5]
    ]

    return {
        "city": data.get(
            "city",
            {},
        ).get(
            "name",
            city,
        ),
        "forecast": forecast
    }

if __name__ == "__main__":
    # mcp_client_py launches this as a stdio subprocess
    mcp.run(
        transport="stdio",
    )