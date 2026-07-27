"""Shared ECS Fargate fleet foundation for tenant Gateway services."""

from __future__ import annotations

from aws_cdk import CfnOutput, CfnParameter, Fn, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct


class FargateFleetStack(Stack):
    """Provision stable shared resources consumed by ``TenantFleetConfig``.

    Per-organization task roles, secrets, task definitions, and ECS services
    remain owned by the Python control-plane reconciler.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc_id = CfnParameter(
            self,
            "VpcId",
            type="AWS::EC2::VPC::Id",
            description="Existing VPC where tenant Gateway Fargate tasks run",
        )
        private_subnet_ids = CfnParameter(
            self,
            "PrivateSubnetIds",
            type="CommaDelimitedList",
            description=(
                "Explicit private subnet IDs for Gateway tasks "
                "(comma-separated; never auto-selected)"
            ),
        )
        s3_file_system_id = CfnParameter(
            self,
            "S3FileSystemId",
            type="String",
            description="Existing S3 Files filesystem ID shared by tenant Gateways",
        )
        s3_file_system_arn = CfnParameter(
            self,
            "S3FileSystemArn",
            type="String",
            description="Existing S3 Files filesystem ARN shared by tenant Gateways",
        )
        gateway_image = CfnParameter(
            self,
            "GatewayImage",
            type="String",
            description="Immutable ECR gateway image URI pinned by sha256 digest",
        )
        credentials_api_url = CfnParameter(
            self,
            "CredentialsApiUrl",
            type="String",
            default="",
            description="Credentials API base URL for Gateway bootstrap (non-secret)",
        )
        resource_prefix = CfnParameter(
            self,
            "ResourcePrefix",
            type="String",
            default="opensre",
            description="Prefix for shared fleet resource names",
        )

        vpc = ec2.Vpc.from_vpc_attributes(
            self,
            "ImportedVpc",
            vpc_id=vpc_id.value_as_string,
            availability_zones=Fn.get_azs(Fn.ref("AWS::Region")),
        )

        cluster = ecs.Cluster(
            self,
            "GatewayCluster",
            cluster_name=Fn.join("", [resource_prefix.value_as_string, "-gateways"]),
            vpc=vpc,
            container_insights=True,
        )
        cluster.enable_fargate_capacity_providers()

        gateway_security_group = ec2.SecurityGroup(
            self,
            "GatewayTaskSecurityGroup",
            vpc=vpc,
            description="Tenant Gateway Fargate tasks - outbound only",
            allow_all_outbound=True,
            security_group_name=Fn.join(
                "",
                [resource_prefix.value_as_string, "-gateway-tasks"],
            ),
        )

        log_group = logs.LogGroup(
            self,
            "GatewayLogGroup",
            log_group_name=Fn.join("", ["/", resource_prefix.value_as_string, "/gateways"]),
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )

        execution_role = iam.Role(
            self,
            "GatewayExecutionRole",
            role_name=Fn.join("", [resource_prefix.value_as_string, "-gateway-execution"]),
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="ECS execution role for tenant Gateway tasks",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy",
                ),
            ],
        )

        self._emit_fleet_outputs(
            execution_role=execution_role,
            gateway_security_group=gateway_security_group,
            log_group=log_group,
            cluster_arn=cluster.cluster_arn,
            private_subnet_ids=private_subnet_ids,
            s3_file_system_id=s3_file_system_id,
            s3_file_system_arn=s3_file_system_arn,
            gateway_image=gateway_image,
            credentials_api_url=credentials_api_url,
        )

    def _emit_fleet_outputs(
        self,
        *,
        cluster_arn: str,
        execution_role: iam.Role,
        gateway_security_group: ec2.SecurityGroup,
        log_group: logs.LogGroup,
        private_subnet_ids: CfnParameter,
        s3_file_system_id: CfnParameter,
        s3_file_system_arn: CfnParameter,
        gateway_image: CfnParameter,
        credentials_api_url: CfnParameter,
    ) -> None:
        """Emit outputs aligned with ``TenantFleetConfig`` environment variables."""
        outputs: tuple[tuple[str, object, str], ...] = (
            ("OpensreFargateClusterArn", cluster_arn, "OPENSRE_FARGATE_CLUSTER_ARN"),
            (
                "OpensreEcsExecutionRoleArn",
                execution_role.role_arn,
                "OPENSRE_ECS_EXECUTION_ROLE_ARN",
            ),
            ("OpensreGatewayLogGroup", log_group.log_group_name, "OPENSRE_GATEWAY_LOG_GROUP"),
            (
                "OpensreFargateSubnetIds",
                Fn.join(",", private_subnet_ids.value_as_list),
                "OPENSRE_FARGATE_SUBNET_IDS",
            ),
            (
                "OpensreFargateSecurityGroupIds",
                gateway_security_group.security_group_id,
                "OPENSRE_FARGATE_SECURITY_GROUP_IDS",
            ),
            (
                "OpensreS3FilesystemId",
                s3_file_system_id.value_as_string,
                "OPENSRE_S3_FILESYSTEM_ID",
            ),
            (
                "OpensreS3FilesystemArn",
                s3_file_system_arn.value_as_string,
                "OPENSRE_S3_FILESYSTEM_ARN",
            ),
            ("OpensreGatewayImage", gateway_image.value_as_string, "OPENSRE_GATEWAY_IMAGE"),
            (
                "OpensreCredentialsApiUrl",
                credentials_api_url.value_as_string,
                "OPENSRE_CREDENTIALS_API_URL",
            ),
        )
        for logical_id, value, env_name in outputs:
            CfnOutput(
                self,
                logical_id,
                value=value,
                description=f"Maps to control-plane env var {env_name}",
                export_name=_fleet_export_name(env_name),
            )


def _fleet_export_name(env_name: str) -> str:
    """Build a CloudFormation export name for a ``TenantFleetConfig`` env var."""
    # Export names allow only alphanumeric characters, colons, and hyphens.
    return f"opensre-fleet:{env_name.replace('_', '-')}"
