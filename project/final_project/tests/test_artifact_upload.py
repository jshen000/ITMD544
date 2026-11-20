"""Offline tests for S3 artifact uploads."""

from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from stack.artifacts import build_artifact, upload_artifact


def missing_object():
    return ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        "HeadObject",
    )


def test_upload_new_artifact(fake_settings):
    artifact = build_artifact(fake_settings.weather)
    s3 = MagicMock()
    s3.head_object.side_effect = missing_object()

    key = upload_artifact(s3, "test-bucket", artifact)

    assert key == artifact.key
    s3.put_object.assert_called_once()
    kwargs = s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "test-bucket"
    assert kwargs["Key"] == artifact.key
    assert kwargs["Body"] == artifact.data
    assert kwargs["Metadata"]["sha256"] == artifact.sha256


def test_existing_artifact_is_reused(fake_settings):
    artifact = build_artifact(fake_settings.advisor)
    s3 = MagicMock()

    key = upload_artifact(s3, "test-bucket", artifact)

    assert key == artifact.key
    s3.put_object.assert_not_called()


def test_access_denied_is_not_ignored(fake_settings):
    artifact = build_artifact(fake_settings.weather)
    s3 = MagicMock()
    s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "403", "Message": "Access Denied"}},
        "HeadObject",
    )

    try:
        upload_artifact(s3, "test-bucket", artifact)
    except ClientError:
        pass
    else:
        raise AssertionError("AccessDenied should not be ignored")

    s3.put_object.assert_not_called()
