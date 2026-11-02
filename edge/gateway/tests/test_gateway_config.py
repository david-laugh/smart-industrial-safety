import pytest

from edge.gateway.config import ConfigError, GatewayConfig, parse_device_tokens


def test_defaults_match_env_example(make_config):
    c = make_config()
    assert (c.bind_addr, c.port) == ("0.0.0.0", 8080)
    assert c.max_request_bytes == 1179648 and c.max_image_bytes == 1048576
    assert c.queue_max_frames == 4 and c.queue_max_bytes == 4194304
    assert c.retry_after_ms == 1000
    assert c.device_tokens["cam-line-a-01"] == "tok-a-0123456789"


def test_token_values_not_in_repr(make_config):
    assert "tok-a" not in repr(make_config())


def test_empty_values_use_defaults(make_config):
    c = make_config(GATEWAY_PORT="", GATEWAY_RETRY_AFTER_MS=" ")
    assert c.port == 8080 and c.retry_after_ms == 1000


@pytest.mark.parametrize(
    "overrides, needle",
    [
        ({"GATEWAY_PORT": "abc"}, "GATEWAY_PORT"),
        ({"GATEWAY_PORT": "70000"}, "GATEWAY_PORT"),
        ({"GATEWAY_QUEUE_MAX_FRAMES": "0"}, "GATEWAY_QUEUE_MAX_FRAMES"),
        ({"GATEWAY_RETRY_AFTER_MS": "-1"}, "GATEWAY_RETRY_AFTER_MS"),
        ({"GATEWAY_MAX_IMAGE_BYTES": "2000000"}, "GATEWAY_MAX_IMAGE_BYTES"),
        ({"GATEWAY_MAX_REQUEST_BYTES": "1000"}, "GATEWAY_MAX_REQUEST_BYTES"),
        ({"GATEWAY_QUEUE_MAX_BYTES": "1000"}, "GATEWAY_QUEUE_MAX_BYTES"),
        ({"LOG_LEVEL": "LOUD"}, "LOG_LEVEL"),
    ],
)
def test_invalid_values_fail(make_config, overrides, needle):
    with pytest.raises(ConfigError, match=needle):
        make_config(**overrides)


def test_tokens_file_required_and_readable(tmp_path):
    with pytest.raises(ConfigError, match="required"):
        GatewayConfig.from_env({})
    with pytest.raises(ConfigError, match="cannot read"):
        GatewayConfig.from_env({"GATEWAY_DEVICE_TOKENS_FILE": str(tmp_path / "nope")})


@pytest.mark.parametrize(
    "text, needle",
    [
        ("", "no device tokens"),
        ("# only comment\n\n", "no device tokens"),
        ("cam-a-01\n", "expected 'device_id=token'"),
        ("CAM_A=tok\n", "invalid device_id"),
        ("cam-a-01=\n", "empty token"),
        ("cam-a-01=to k\n", "whitespace"),
        ("cam-a-01=t1\ncam-a-01=t2\n", "duplicate device_id"),
        ("cam-a-01=t1\ncam-b-01=t1\n", "already used"),
    ],
)
def test_bad_tokens_file(text, needle):
    with pytest.raises(ConfigError, match=needle) as ei:
        parse_device_tokens(text)
    assert "t1" not in str(ei.value) and "t2" not in str(ei.value)  # 토큰 값은 메시지에 넣지 않음


def test_tokens_parse_strips_and_skips_comments():
    assert parse_device_tokens("\n# c\n cam-a-01 = s3cret \n") == {"cam-a-01": "s3cret"}
