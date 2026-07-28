"""Baseline CloudWatch alarms for the public forwarder API."""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class PublicForwarderMonitoring(Construct):
    """Create actionable error and throttling alarms without notification coupling."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        function: lambda_.Function,
        api_id: str,
    ) -> None:
        super().__init__(scope, construct_id)

        cloudwatch.Alarm(
            self,
            "LambdaErrors",
            metric=function.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        cloudwatch.Alarm(
            self,
            "LambdaThrottles",
            metric=function.metric_throttles(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        cloudwatch.Alarm(
            self,
            "HttpApiServerErrors",
            metric=cloudwatch.Metric(
                namespace="AWS/ApiGateway",
                metric_name="5xx",
                dimensions_map={"ApiId": api_id, "Stage": "$default"},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
