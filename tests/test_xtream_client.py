import responses

from app.xtream_client import AuthenticationError, XtreamClient


@responses.activate
def test_connection_and_credentials_are_sent_as_query_params() -> None:
    responses.get("https://example.test/player_api.php", json={"user_info": {"auth": 1, "status": "Active"}})
    client = XtreamClient("https://example.test/", "alice", "secret")
    assert client.test_connection()["status"] == "Active"
    request_url = responses.calls[0].request.url
    assert "username=alice" in request_url
    assert "password=secret" in request_url


@responses.activate
def test_invalid_credentials() -> None:
    responses.get("https://example.test/player_api.php", json={"user_info": {"auth": 0}})
    client = XtreamClient("https://example.test", "bad", "bad")
    try:
        client.test_connection()
    except AuthenticationError:
        pass
    else:
        raise AssertionError("AuthenticationError expected")


def test_stream_url_prefers_extension_then_no_extension() -> None:
    client = XtreamClient("https://example.test", "u name", "p@ss")
    assert client.stream_urls("movie", 42, "mkv") == [
        "https://example.test/movie/u%20name/p%40ss/42.mkv",
        "https://example.test/movie/u%20name/p%40ss/42",
    ]
    assert client.stream_urls("series", 7, None) == ["https://example.test/series/u%20name/p%40ss/7"]


@responses.activate
def test_get_categories() -> None:
    responses.get(
        "https://example.test/player_api.php",
        json=[{"category_id": "1", "category_name": "NETFLIX", "parent_id": 0}],
    )
    responses.get(
        "https://example.test/player_api.php",
        json=[{"category_id": "2", "category_name": "LANCAMENTOS", "parent_id": 0}],
    )
    client = XtreamClient("https://example.test", "alice", "secret")
    series_cats = client.get_series_categories()
    assert len(series_cats) == 1
    assert series_cats[0]["category_name"] == "NETFLIX"

    vod_cats = client.get_vod_categories()
    assert len(vod_cats) == 1
    assert vod_cats[0]["category_name"] == "LANCAMENTOS"

