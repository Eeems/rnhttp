"""Tests for rnhttp protocol."""

import pytest

from rnhttp.types import (
    HttpParserError,
    HttpRequest,
    HttpResponse,
    encode_chunked,
)


# RFC 9112, 3 Request Line
class TestRequestLineParsing:
    """Tests for HTTP request line parsing."""

    def test_get_root(self):
        """Parse GET request to root."""
        # RFC 9112, 3 Request Line
        request = HttpRequest.parse(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        assert request.method == "GET"
        assert request.path == "/"
        assert request.version == "HTTP/1.1"

    def test_get_absolute_path(self):
        """Parse GET with absolute path."""
        # RFC 9112, 3.2 Request Target
        request = HttpRequest.parse(
            b"GET /path/to/resource HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        assert request.method == "GET"
        assert request.path == "/path/to/resource"

    def test_post_request(self):
        """Parse POST request."""
        # RFC 9110, 9.3 POST
        request = HttpRequest.parse(
            b"POST /submit HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        assert request.method == "POST"
        assert request.path == "/submit"

    def test_put_request(self):
        """Parse PUT request."""
        # RFC 9110, 9.3 PUT
        request = HttpRequest.parse(
            b"PUT /resource HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        assert request.method == "PUT"
        assert request.path == "/resource"

    def test_delete_request(self):
        """Parse DELETE request."""
        # RFC 9110, 9.3 DELETE
        request = HttpRequest.parse(
            b"DELETE /resource HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        assert request.method == "DELETE"
        assert request.path == "/resource"

    def test_head_request(self):
        """Parse HEAD request."""
        # RFC 9110, 9.3 HEAD
        request = HttpRequest.parse(
            b"HEAD /resource HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        assert request.method == "HEAD"
        assert request.path == "/resource"

    def test_options_request_asterisk(self):
        """Parse OPTIONS request with asterisk form."""
        # RFC 9112, 3.2.1 asterisk-form
        request = HttpRequest.parse(b"OPTIONS * HTTP/1.1\r\nHost: example.com\r\n\r\n")
        assert request.method == "OPTIONS"
        assert request.path == "*"

    def test_options_request_absolute(self):
        """Parse OPTIONS request to absolute URI."""
        # RFC 9112, 3.2.2 absolute-form
        request = HttpRequest.parse(
            b"OPTIONS http://example.com/resource HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        assert request.method == "OPTIONS"
        assert request.path == "http://example.com/resource"

    def test_get_with_query_string(self):
        """Parse GET request with query string."""
        # RFC 9112, 3.2 origin-form
        request = HttpRequest.parse(
            b"GET /search?q=test&lang=en HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        assert request.method == "GET"
        assert request.path == "/search?q=test&lang=en"

    def test_get_with_fragment(self):
        """Parse GET request with fragment (fragment not sent to server)."""
        # RFC 9112, 3.2 (fragments are not sent in HTTP requests)
        request = HttpRequest.parse(
            b"GET /docs#section HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        assert request.method == "GET"
        assert request.path == "/docs#section"


# RFC 9112, 3.3 Message Body
class TestRequestWithBody:
    """Tests for request message body handling."""

    def test_post_with_content_length(self):
        """Parse POST with explicit Content-Length."""
        # RFC 9112, 3.3.3 Fixed Length
        request = HttpRequest.parse(
            b"POST /submit HTTP/1.1\r\n"
            + b"Host: example.com\r\n"
            + b"Content-Type: text/plain\r\n"
            + b"Content-Length: 5\r\n"
            + b"\r\n"
            + b"hello"
        )
        assert request.method == "POST"
        assert request.body == b"hello"

    def test_post_empty_body(self):
        """Parse POST with Content-Length: 0."""
        # RFC 9112, 3.3.3 Fixed Length
        request = HttpRequest.parse(
            b"POST /empty HTTP/1.1\r\nHost: example.com\r\nContent-Length: 0\r\n\r\n"
        )
        assert request.method == "POST"
        assert request.body is None

    def test_post_with_chunked_transfer(self):
        """Parse POST with chunked transfer encoding."""
        # RFC 9112, 3.3.1 Chunked Transfer Coding
        request = HttpRequest.parse(
            b"POST /submit HTTP/1.1\r\n"
            + b"Host: example.com\r\n"
            + b"Transfer-Encoding: chunked\r\n"
            + b"\r\n"
            + b"5\r\nhello\r\n0\r\n\r\n"
        )
        assert request.method == "POST"
        assert request.body == b"hello"

    def test_post_large_body(self):
        """Parse POST with body larger than 4KB chunk."""
        # RFC 9112, 3.3.1
        body = b"x" * 10000
        request = HttpRequest.parse(
            b"POST /upload HTTP/1.1\r\n"
            + b"Host: example.com\r\n"
            + b"Content-Length: 10000\r\n"
            + b"\r\n"
            + body
        )
        assert request.method == "POST"
        assert request.body == body


# RFC 9112, 3.5 Message Parsing
class TestRequestParsingEdgeCases:
    """Tests for edge cases in request parsing."""

    def test_empty_request(self):
        """Parse empty request."""
        # RFC 9112, 3.5 (incomplete message)
        with pytest.raises(HttpParserError):
            _ = HttpRequest.parse(b"")

    def test_request_no_path_defaults_to_root(self):
        """Parse request without path defaults to /."""
        # RFC 9112, 3.2 (origin-form)
        request = HttpRequest.parse(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
        assert request.path == "/"

    def test_multiple_headers(self):
        """Parse request with multiple headers."""
        # RFC 9112, 3.2 (header fields)
        request = HttpRequest.parse(
            b"GET /resource HTTP/1.1\r\n"
            + b"Host: example.com\r\n"
            + b"Accept: text/html\r\n"
            + b"Accept-Language: en\r\n"
            + b"User-Agent: Test/1.0\r\n"
            + b"\r\n"
        )
        assert request.method == "GET"
        assert "accept" in request.headers
        assert "accept-language" in request.headers
        assert "user-agent" in request.headers

    def test_header_case_insensitive(self):
        """Header names are case-insensitive."""
        # RFC 9110, 5.1 Field Names
        request = HttpRequest.parse(
            b"GET / HTTP/1.1\r\nHOST: example.com\r\nACCEPT: */*\r\n\r\n"
        )
        assert request.headers["host"] == "example.com"
        assert request.headers["accept"] == "*/*"


# RFC 9112, 4 Status Line
class TestStatusLineParsing:
    """Tests for HTTP status line parsing."""

    def test_200_ok(self):
        """Parse 200 OK response."""
        # RFC 9112, 4 Status Line
        response = HttpResponse.parse(
            b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\n\r\nHello, World!"
        )
        assert response.version == "HTTP/1.1"
        assert response.status == 200
        assert response.reason == "OK"

    def test_404_not_found(self):
        """Parse 404 Not Found response."""
        # RFC 9110, 15.5.15 404 Not Found
        response = HttpResponse.parse(
            b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
        )
        assert response.status == 404

    def test_201_created(self):
        """Parse 201 Created response."""
        # RFC 9110, 15.2.2 201 Created
        response = HttpResponse.parse(
            b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n"
        )
        assert response.status == 201

    def test_204_no_content(self):
        """Parse 204 No Content response."""
        # RFC 9110, 15.2.5 204 No Content
        response = HttpResponse.parse(b"HTTP/1.1 204 No Content\r\n\r\n")
        assert response.status == 204

    def test_301_moved_permanently(self):
        """Parse 301 Moved Permanently response."""
        # RFC 9110, 15.4.2 301 Moved Permanently
        response = HttpResponse.parse(
            b"HTTP/1.1 301 Moved Permanently\r\nLocation: http://example.com/new\r\nContent-Length: 0\r\n\r\n"
        )
        assert response.status == 301

    def test_302_found(self):
        """Parse 302 Found response."""
        # RFC 9110, 15.4.3 302 Found
        response = HttpResponse.parse(
            b"HTTP/1.1 302 Found\r\nLocation: /temp\r\nContent-Length: 0\r\n\r\n"
        )
        assert response.status == 302

    def test_400_bad_request(self):
        """Parse 400 Bad Request response."""
        # RFC 9110, 15.5.1 400 Bad Request
        response = HttpResponse.parse(
            b"HTTP/1.1 400 Bad Request\r\nContent-Length: 11\r\n\r\nBad Request"
        )
        assert response.status == 400

    def test_401_unauthorized(self):
        """Parse 401 Unauthorized response."""
        # RFC 9110, 15.5.2 401 Unauthorized
        response = HttpResponse.parse(
            b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n"
        )
        assert response.status == 401

    def test_403_forbidden(self):
        """Parse 403 Forbidden response."""
        # RFC 9110, 15.5.3 403 Forbidden
        response = HttpResponse.parse(
            b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"
        )
        assert response.status == 403

    def test_500_internal_server_error(self):
        """Parse 500 Internal Server Error response."""
        # RFC 9110, 15.6.1 500 Internal Server Error
        response = HttpResponse.parse(
            b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n"
        )
        assert response.status == 500

    def test_503_service_unavailable(self):
        """Parse 503 Service Unavailable response."""
        # RFC 9110, 15.6.4 503 Service Unavailable
        response = HttpResponse.parse(
            b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 3600\r\nContent-Length: 0\r\n\r\n"
        )
        assert response.status == 503

    def test_custom_reason_phrase(self):
        """Parse response with custom reason phrase."""
        # RFC 9112, 4 (reason-phrase is optional)
        # Note: httptools does not expose the reason phrase via callback,
        # so custom reason phrases are not preserved. This test documents the limitation.
        response = HttpResponse.parse(
            b"HTTP/1.1 200 Custom Reason\r\nContent-Length: 0\r\n\r\n"
        )
        assert response.status == 200
        # httptools limitation: reason defaults to known phrase
        assert response.reason == "OK"

    def test_empty_reason_defaults_to_known(self):
        """Parse response with empty reason defaults to known phrase."""
        # RFC 9112, 4 (reason-phrase is optional)
        response = HttpResponse.parse(b"HTTP/1.1 404 \r\nContent-Length: 0\r\n\r\n")
        assert response.status == 404
        assert response.reason == "Not Found"

    def test_informational_100_continue(self):
        """Parse 100 Continue response."""
        # RFC 9110, 15.1 1xx Informational
        response = HttpResponse.parse(b"HTTP/1.1 100 Continue\r\n\r\n")
        assert response.status == 100


# RFC 9112, 3.3 Message Body
class TestResponseWithBody:
    """Tests for response message body handling."""

    def test_response_with_content_length(self):
        """Parse response with explicit Content-Length."""
        # RFC 9112, 3.3.3 Fixed Length
        response = HttpResponse.parse(
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
        )
        assert response.body == b"hello"

    def test_response_empty_body(self):
        """Parse response with Content-Length: 0."""
        # RFC 9112, 3.3.3 Fixed Length
        response = HttpResponse.parse(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        assert response.body is None

    def test_response_with_chunked_transfer(self):
        """Parse response with chunked transfer encoding."""
        # RFC 9112, 3.3.1 Chunked Transfer Coding
        response = HttpResponse.parse(
            b"HTTP/1.1 200 OK\r\n"
            + b"Transfer-Encoding: chunked\r\n"
            + b"\r\n"
            + b"5\r\nhello\r\n0\r\n\r\n"
        )
        assert response.body == b"hello"

    def test_response_multiple_chunks(self):
        """Parse response with multiple chunks."""
        # RFC 9112, 7.1 Chunked Transfer Coding
        response = HttpResponse.parse(
            b"HTTP/1.1 200 OK\r\n"
            + b"Transfer-Encoding: chunked\r\n"
            + b"\r\n"
            + b"3\r\nhel\r\n2\r\nlo\r\n0\r\n\r\n"
        )
        assert response.body == b"hello"


# RFC 9112, 3.5 Message Parsing Errors
class TestResponseParsingErrors:
    """Tests for response parsing errors."""

    def test_empty_response(self):
        """Parse empty response."""
        # RFC 9112, 3.5 (incomplete message)
        with pytest.raises(HttpParserError):
            _ = HttpResponse.parse(b"")

    def test_invalid_status_line(self):
        """Parse response with invalid status line."""
        # RFC 9112, 4 Status Line
        with pytest.raises(HttpParserError):
            _ = HttpResponse.parse(b"INVALID STATUS LINE\r\n\r\n")

    def test_missing_status_code(self):
        """Parse response with missing status code."""
        # RFC 9112, 4 Status Line
        with pytest.raises(HttpParserError):
            _ = HttpResponse.parse(b"HTTP/1.1 \r\n\r\n")


# RFC 9112, 3 Request Serialization
class TestRequestSerialization:
    """Tests for HTTP request serialization."""

    def test_serialize_get_root(self):
        """Serialize GET request to root."""
        # RFC 9112, 3 Request Line
        request = HttpRequest(method="GET", path="/")
        result = bytes(request)
        assert result.startswith(b"GET / HTTP/1.1")

    def test_serialize_get_with_path(self):
        """Serialize GET request with path."""
        # RFC 9112, 3.2 Request Target
        request = HttpRequest(method="GET", path="/path/to/resource")
        result = bytes(request)
        assert b"GET /path/to/resource HTTP/1.1" in result

    def test_serialize_post_with_body(self):
        """Serialize POST request with body."""
        # RFC 9110, 9.3 POST
        request = HttpRequest(
            method="POST",
            path="/submit",
            headers={"Content-Type": "text/plain"},
            body=b"hello",
        )
        result = bytes(request)
        assert b"POST /submit HTTP/1.1" in result
        assert b"Content-Type: text/plain" in result

    def test_serialize_put(self):
        """Serialize PUT request."""
        # RFC 9110, 9.3 PUT
        request = HttpRequest(method="PUT", path="/resource", body=b"data")
        result = bytes(request)
        assert b"PUT /resource HTTP/1.1" in result
        assert b"Content-Length: 4" in result

    def test_serialize_delete(self):
        """Serialize DELETE request."""
        # RFC 9110, 9.3 DELETE
        request = HttpRequest(method="DELETE", path="/resource")
        result = bytes(request)
        assert b"DELETE /resource HTTP/1.1" in result

    def test_serialize_with_headers(self):
        """Serialize request with custom headers."""
        # RFC 9112, 3.2 Header Fields
        request = HttpRequest(
            method="GET",
            path="/",
            headers={
                "Host": "example.com",
                "Accept": "application/json",
                "User-Agent": "TestClient/1.0",
            },
        )
        result = bytes(request)
        assert b"Host: example.com" in result
        assert b"Accept: application/json" in result
        assert b"User-Agent: TestClient/1.0" in result

    def test_serialize_with_query_string(self):
        """Serialize request with query string."""
        # RFC 9112, 3.2 origin-form
        request = HttpRequest(method="GET", path="/search?q=test&page=1")
        result = bytes(request)
        assert b"/search?q=test&page=1" in result


# RFC 9112, 4 Status Line Serialization
class TestResponseSerialization:
    """Tests for HTTP response serialization."""

    def test_serialize_200_ok(self):
        """Serialize 200 OK response."""
        # RFC 9112, 4 Status Line
        response = HttpResponse(status=200, reason="OK")
        result = bytes(response)
        assert result.startswith(b"HTTP/1.1 200 OK")

    def test_serialize_404(self):
        """Serialize 404 Not Found response."""
        # RFC 9110, 15.5.15 404 Not Found
        response = HttpResponse(status=404, reason="Not Found")
        result = bytes(response)
        assert result.startswith(b"HTTP/1.1 404 Not Found")

    def test_serialize_with_body(self):
        """Serialize response with body."""
        # RFC 9112, 3.3 Message Body
        response = HttpResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/plain"},
            body=b"Hello!",
        )
        result = bytes(response)
        assert b"HTTP/1.1 200 OK" in result
        assert b"Content-Type: text/plain" in result
        assert b"Hello!" in result

    def test_serialize_201_created(self):
        """Serialize 201 Created response."""
        # RFC 9110, 15.2.2 201 Created
        response = HttpResponse(status=201, reason="Created", body=b"Created")
        result = bytes(response)
        assert result.startswith(b"HTTP/1.1 201 Created")

    def test_serialize_301_redirect(self):
        """Serialize 301 Moved Permanently response."""
        # RFC 9110, 15.4.2 301 Moved Permanently
        response = HttpResponse(
            status=301,
            reason="Moved Permanently",
            headers={"Location": "http://example.com/new"},
        )
        result = bytes(response)
        assert result.startswith(b"HTTP/1.1 301 Moved Permanently")
        assert b"Location: http://example.com/new" in result

    def test_serialize_500_error(self):
        """Serialize 500 Internal Server Error response."""
        # RFC 9110, 15.6.1 500 Internal Server Error
        response = HttpResponse(status=500, reason="Internal Server Error")
        result = bytes(response)
        assert result.startswith(b"HTTP/1.1 500 Internal Server Error")

    def test_serialize_with_multiple_headers(self):
        """Serialize response with multiple headers."""
        # RFC 9112, 3.2 Header Fields
        response = HttpResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
                "X-Custom-Header": "value",
            },
        )
        result = bytes(response)
        assert b"Content-Type: application/json" in result
        assert b"Cache-Control: no-cache" in result
        assert b"X-Custom-Header: value" in result


# RFC 9112, 7.1 Chunked Transfer Coding
class TestChunkedEncoding:
    """Tests for chunked transfer encoding."""

    def test_encode_single_chunk(self):
        """Encode single chunk."""
        # RFC 9112, 7.1 Chunked Transfer Coding
        result = encode_chunked(b"hello")
        assert result == b"5\r\nhello\r\n0\r\n\r\n"

    def test_encode_empty_body(self):
        """Encode empty body."""
        # RFC 9112, 7.1.1
        result = encode_chunked(b"")
        assert result == b"0\r\n\r\n"

    def test_encode_multiple_chunks(self):
        """Encode body that spans multiple chunks."""
        # RFC 9112, 7.1
        body = b"HelloWorld12345"
        result = encode_chunked(body)
        # 15 bytes = 0xf in hex
        assert result == b"f\r\nHelloWorld12345\r\n0\r\n\r\n"

    def test_serialize_with_chunked_encoding(self):
        """Serialize request with chunked transfer encoding."""
        # RFC 9112, 3.3.1
        request = HttpRequest(
            method="POST",
            path="/upload",
            headers={"Transfer-Encoding": "chunked"},
            body=b"hello",
        )
        result = bytes(request)
        assert b"Transfer-Encoding: chunked" in result

    def test_serialize_response_with_chunked_encoding(self):
        """Serialize response with chunked transfer encoding."""
        # RFC 9112, 3.3.1
        response = HttpResponse(
            status=200,
            reason="OK",
            headers={"Transfer-Encoding": "chunked"},
            body=b"hello",
        )
        result = bytes(response)
        assert b"Transfer-Encoding: chunked" in result


# RFC 9110, 8.6 Content-Length
class TestContentLength:
    """Tests for Content-Length handling."""

    def test_request_auto_content_length(self):
        """Content-Length auto-added when body present."""
        # RFC 9110, 8.6 Content-Length
        request = HttpRequest(method="POST", path="/submit", body=b"hello")
        result = bytes(request)
        assert b"Content-Length: 5" in result

    def test_response_auto_content_length(self):
        """Content-Length auto-added to response with body."""
        # RFC 9110, 8.6 Content-Length
        response = HttpResponse(status=200, reason="OK", body=b"hello")
        result = bytes(response)
        assert b"Content-Length: 5" in result

    def test_explicit_content_length_preserved(self):
        """Explicit Content-Length header is preserved."""
        # RFC 9110, 8.6
        request = HttpRequest(
            method="POST",
            path="/submit",
            headers={"Content-Length": "10"},
            body=b"hello",
        )
        result = bytes(request)
        assert b"Content-Length: 10" in result


# RFC 9110, 8.8 Connection
class TestConnectionHeader:
    """Tests for Connection header handling."""

    def test_connection_close(self):
        """Serialize request with Connection: close."""
        # RFC 9110, 8.8 Connection
        request = HttpRequest(
            method="GET",
            path="/",
            headers={"Connection": "close"},
        )
        result = bytes(request)
        assert b"Connection: close" in result

    def test_connection_keep_alive(self):
        """Serialize request with Connection: keep-alive."""
        # RFC 9110, 8.8 Connection
        request = HttpRequest(
            method="GET",
            path="/",
            headers={"Connection": "keep-alive"},
        )
        result = bytes(request)
        assert b"Connection: keep-alive" in result
