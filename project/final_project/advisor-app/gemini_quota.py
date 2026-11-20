"""Atomic daily Gemini request quota."""

from datetime import datetime, timedelta, timezone
from botocore.exceptions import ClientError


def reserve_request(client, table_name, daily_limit=100):
    if not isinstance(daily_limit, int) or isinstance(daily_limit, bool) or daily_limit < 1:
        raise ValueError("Invalid daily limit")

    now = datetime.now(timezone.utc)
    tomorrow = now.date() + timedelta(days=1)
    expires = int(
        datetime.combine(
            tomorrow,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
    ) + 86400

    try:
        response = client.update_item(
            TableName=table_name,
            Key={"pk": {"S": f"gemini_quota#{now:%Y-%m-%d}"}},
            UpdateExpression=(
                "SET #count = if_not_exists(#count, :zero) + :one, "
                "#expires = :expires"
            ),
            ConditionExpression=(
                "attribute_not_exists(#count) OR #count < :limit"
            ),
            ExpressionAttributeNames={
                "#count": "request_count",
                "#expires": "expires",
            },
            ExpressionAttributeValues={
                ":zero": {"N": "0"},
                ":one": {"N": "1"},
                ":limit": {"N": str(daily_limit)},
                ":expires": {"N": str(expires)},
            },
            ReturnValues="UPDATED_NEW",
        )
        return {
            "allowed": True,
            "count": int(response["Attributes"]["request_count"]["N"]),
        }

    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"allowed": False, "count": None}
        raise
