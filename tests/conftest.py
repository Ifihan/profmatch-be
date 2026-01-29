"""Pytest configuration and shared fixtures."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# Configure pytest-asyncio
pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_publication_data() -> list[dict[str, Any]]:
    """Sample publication data for testing."""
    return [
        {
            "title": "Deep Learning for Natural Language Processing",
            "authors": ["John Doe", "Jane Smith"],
            "year": 2023,
            "venue": "NeurIPS",
            "abstract": "A paper about NLP",
            "citation_count": 150,
            "url": "https://example.com/paper1",
        },
        {
            "title": "Reinforcement Learning in Robotics",
            "authors": ["John Doe"],
            "year": 2022,
            "venue": "ICRA",
            "abstract": "A paper about robotics",
            "citation_count": 75,
            "url": "https://example.com/paper2",
        },
    ]


@pytest.fixture
def sample_professor_data() -> dict[str, Any]:
    """Sample professor data for testing."""
    return {
        "id": str(uuid4()),
        "name": "Dr. John Doe",
        "title": "Associate Professor",
        "department": "Computer Science",
        "university": "MIT",
        "email": "john.doe@mit.edu",
        "scholar_id": "ABC123",
        "research_areas": ["machine learning", "NLP", "robotics"],
        "publications": [],
        "citation_metrics": {"h_index": 25, "i10_index": 40, "total_citations": 5000},
        "last_updated": datetime.utcnow().isoformat(),
    }


@pytest.fixture
def sample_faculty_data() -> list[dict[str, Any]]:
    """Sample faculty data from university directory."""
    return [
        {
            "name": "Dr. Alice Smith",
            "title": "Professor",
            "department": "Computer Science",
            "email": "alice@example.edu",
            "profile_url": "https://example.edu/alice",
        },
        {
            "name": "Dr. Bob Jones",
            "title": "Assistant Professor",
            "department": "Electrical Engineering",
            "email": "bob@example.edu",
            "profile_url": "https://example.edu/bob",
        },
        {
            "name": "Dr. Carol Williams",
            "title": "Associate Professor",
            "department": "Computer Science",
            "email": "carol@example.edu",
            "profile_url": "https://example.edu/carol",
        },
    ]


@pytest.fixture
def sample_cv_data() -> dict[str, Any]:
    """Sample parsed CV data."""
    return {
        "education": [
            {
                "institution": "Stanford University",
                "degree": "PhD",
                "field": "Computer Science",
                "year": 2020,
            },
            {
                "institution": "MIT",
                "degree": "BS",
                "field": "Mathematics",
                "year": 2015,
            },
        ],
        "experience": [
            {
                "organization": "Google",
                "role": "Research Intern",
                "description": "Worked on ML models",
                "start_year": 2019,
                "end_year": 2020,
            },
        ],
        "publications": [
            {
                "title": "My Research Paper",
                "authors": ["Me", "Advisor"],
                "year": 2020,
                "venue": "ICML",
            },
        ],
        "skills": ["Python", "TensorFlow", "PyTorch"],
        "research_interests": ["machine learning", "computer vision"],
    }


@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    storage = {}

    async def mock_get(key):
        return storage.get(key)

    async def mock_setex(key, ttl, value):
        storage[key] = value

    async def mock_delete(key):
        if key in storage:
            del storage[key]
            return 1
        return 0

    client = AsyncMock()
    client.get = mock_get
    client.setex = mock_setex
    client.delete = mock_delete
    client.ping = AsyncMock()

    return client, storage


@pytest.fixture
def mock_gcs_bucket():
    """Mock GCS bucket for testing."""
    blobs = {}

    class MockBlob:
        def __init__(self, name):
            self.name = name
            self.metadata = {}
            self.time_created = datetime.utcnow()

        def upload_from_string(self, content):
            blobs[self.name] = {"content": content, "metadata": self.metadata}

        def download_to_filename(self, filename):
            if self.name in blobs:
                with open(filename, "wb") as f:
                    f.write(blobs[self.name]["content"])

        def exists(self):
            return self.name in blobs

    class MockBucket:
        def blob(self, name):
            return MockBlob(name)

        def list_blobs(self, prefix=None):
            result = []
            for name in blobs:
                if prefix is None or name.startswith(prefix):
                    result.append(MockBlob(name))
            return result

        def delete_blobs(self, blob_list):
            for blob in blob_list:
                if blob.name in blobs:
                    del blobs[blob.name]

    return MockBucket(), blobs


@pytest.fixture
async def test_app() -> AsyncGenerator[AsyncClient, None]:
    """Create test client for FastAPI app."""
    # Import here to avoid loading app before mocks are in place
    with patch.dict("os.environ", {
        "REDIS_URL": "redis://localhost:6379",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "GEMINI_API_KEY": "test-key",
        "GCS_BUCKET_NAME": "test-bucket",
        "GCS_PROJECT_ID": "test-project",
    }):
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
