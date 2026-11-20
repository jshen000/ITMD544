"""Offline DynamoDB lifecycle tests using Moto."""

import os
import unittest
from unittest.mock import patch

from moto import mock_aws

from stack.clients import AwsClients
from stack.storage import ensure_table, delete_table


class DynamoDBLifecycleTests(unittest.TestCase):
    """Verify repeatable creation, TTL configuration and deletion."""

    def test_create_twice_delete_twice(self):
        credentials = {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_SESSION_TOKEN": "testing",
            "AWS_EC2_METADATA_DISABLED": "true",
        }

        with patch.dict(os.environ, credentials):
            with mock_aws():
                aws = AwsClients.create("us-east-2")
                name = "final-weather-facts-test"

                first = ensure_table(aws, name)
                second = ensure_table(aws, name)

                self.assertEqual(first, second)
                self.assertEqual(
                    aws.dynamodb.list_tables()["TableNames"],
                    [name],
                )

                ttl = aws.dynamodb.describe_time_to_live(
                    TableName=name
                )["TimeToLiveDescription"]

                self.assertEqual(ttl["AttributeName"], "expires")
                self.assertIn(
                    ttl["TimeToLiveStatus"],
                    ("ENABLED", "ENABLING"),
                )

                delete_table(aws, name)
                self.assertNotIn(
                    name,
                    aws.dynamodb.list_tables()["TableNames"],
                )

                delete_table(aws, name)


if __name__ == "__main__":
    unittest.main()
