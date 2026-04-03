"""Tests for rnhttp types."""

from rnhttp.types import (
    HttpRequest,
    HttpResponse,
    reason_text,
)


class TestHttpRequest:
    """Tests for HttpRequest class."""

    def test_default_values(self):
        """Test default values for HttpRequest."""
        request = HttpRequest(method="GET", path="/")

        assert request.method == "GET"
        assert request.path == "/"
        assert request.version == "HTTP/1.1"
        assert request.headers == {}
        assert request.body is None

    def test_with_headers(self):
        """Test HttpRequest with headers."""
        request = HttpRequest(
            method="POST",
            path="/api/data",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=b'{"key": "value"}',
        )

        assert request.method == "POST"
        assert request.path == "/api/data"
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["Accept"] == "application/json"
        assert request.body == b'{"key": "value"}'

    def test_repr(self):
        """Test HttpRequest __repr__."""
        request = HttpRequest(method="GET", path="/", body=b"hello")

        repr_str = repr(request)
        assert "GET" in repr_str
        assert "/" in repr_str
        assert "HTTP/1.1" in repr_str

    def test_equality(self):
        """Test HttpRequest equality."""
        request1 = HttpRequest(method="GET", path="/", body=b"hello")
        request2 = HttpRequest(method="GET", path="/", body=b"hello")
        request3 = HttpRequest(method="POST", path="/", body=b"hello")

        assert request1 == request2
        assert request1 != request3

    def test_inequality_with_non_request(self):
        """Test HttpRequest inequality with non-HttpRequest."""
        request = HttpRequest(method="GET", path="/")
        assert request != "GET / HTTP/1.1"


class TestHttpResponse:
    """Tests for HttpResponse class."""

    def test_default_values(self):
        """Test default values for HttpResponse."""
        response = HttpResponse(status=200)

        assert response.version == "HTTP/1.1"
        assert response.status == 200
        assert response.reason == "OK"
        assert response.headers == {}
        assert response.body is None

    def test_custom_reason(self):
        """Test HttpResponse with custom reason."""
        response = HttpResponse(status=404, reason="Not Found")

        assert response.status == 404
        assert response.reason == "Not Found"

    def test_default_reason_phrases(self):
        """Test default reason phrases for common status codes."""
        assert reason_text(100) == "Continue"
        assert reason_text(200) == "OK"
        assert reason_text(201) == "Created"
        assert reason_text(204) == "No Content"
        assert reason_text(301) == "Moved Permanently"
        assert reason_text(302) == "Found"
        assert reason_text(304) == "Not Modified"
        assert reason_text(400) == "Bad Request"
        assert reason_text(401) == "Unauthorized"
        assert reason_text(403) == "Forbidden"
        assert reason_text(404) == "Not Found"
        assert reason_text(405) == "Method Not Allowed"
        assert reason_text(408) == "Request Timeout"
        assert reason_text(409) == "Conflict"
        assert reason_text(413) == "Payload Too Large"
        assert reason_text(414) == "URI Too Long"
        assert reason_text(500) == "Internal Server Error"
        assert reason_text(501) == "Not Implemented"
        assert reason_text(502) == "Bad Gateway"
        assert reason_text(503) == "Service Unavailable"
        assert reason_text(504) == "Gateway Timeout"

    def test_unknown_status_code(self):
        """Test unknown status code returns default reason."""
        response = HttpResponse(status=999)
        assert response.reason == "Unknown"

    def test_with_headers_and_body(self):
        """Test HttpResponse with headers and body."""
        response = HttpResponse(
            status=201,
            reason="Created",
            headers={"Location": "/new-resource", "Content-Type": "application/json"},
            body=b'{"id": 123}',
        )

        assert response.status == 201
        assert response.reason == "Created"
        assert response.headers["Location"] == "/new-resource"
        assert response.headers["Content-Type"] == "application/json"
        assert response.body == b'{"id": 123}'

    def test_repr(self):
        """Test HttpResponse __repr__."""
        response = HttpResponse(status=200, body=b"hello")

        repr_str = repr(response)
        assert "200" in repr_str
        assert "OK" in repr_str
        assert "HTTP/1.1" in repr_str

    def test_equality(self):
        """Test HttpResponse equality."""
        response1 = HttpResponse(status=200, reason="OK", body=b"hello")
        response2 = HttpResponse(status=200, reason="OK", body=b"hello")
        response3 = HttpResponse(status=404, reason="Not Found", body=b"not found")

        assert response1 == response2
        assert response1 != response3

    def test_inequality_with_non_response(self):
        """Test HttpResponse inequality with non-HttpResponse."""
        response = HttpResponse(status=200)
        assert response != "HTTP/1.1 200 OK"
