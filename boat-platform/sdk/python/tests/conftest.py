import pytest

from boat.client import BoAtClient
from boat.scenario_builder import ScenarioBuilder


@pytest.fixture
def boat_client():
    client = BoAtClient(address="localhost:50051")
    yield client
    client.close()


@pytest.fixture
def scenario_builder():
    return ScenarioBuilder(tick_rate_hz=100)
