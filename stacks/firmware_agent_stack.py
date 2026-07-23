"""CDK stack definition for the IoT Firmware Orchestration Agent."""

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from constructs import Construct


class FirmwareAgentStack(Stack):
    """Single CDK stack containing all resources for the firmware orchestration agent."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        """Initialize the FirmwareAgentStack."""
        super().__init__(scope, construct_id, **kwargs)

        self.fleet_inventory_table = self._create_fleet_inventory_table()
        self.deployment_history_table = self._create_deployment_history_table()
        self.firmware_bucket = self._create_firmware_bucket()
        self.notification_topic = self._create_notification_topic()
        self.agent_lambda = self._create_agent_lambda()
        self.state_machine = self._create_state_machine()
        self._create_eventbridge_rule()
        self._create_outputs()

    def _create_fleet_inventory_table(self) -> dynamodb.Table:
        """Create the FleetInventory DynamoDB table with GSIs."""
        table = dynamodb.Table(
            self,
            "FleetInventoryTable",
            table_name="FleetInventory",
            partition_key=dynamodb.Attribute(name="thing_name", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # GSI-1: device_type-index
        table.add_global_secondary_index(
            index_name="device_type-index",
            partition_key=dynamodb.Attribute(name="device_type", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="thing_name", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI-2: facility-index
        table.add_global_secondary_index(
            index_name="facility-index",
            partition_key=dynamodb.Attribute(name="facility", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="thing_name", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        return table

    def _create_deployment_history_table(self) -> dynamodb.Table:
        """Create the DeploymentHistory DynamoDB table."""
        table = dynamodb.Table(
            self,
            "DeploymentHistoryTable",
            table_name="DeploymentHistory",
            partition_key=dynamodb.Attribute(name="deployment_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        return table

    def _create_firmware_bucket(self) -> s3.Bucket:
        """Create the S3 bucket for firmware storage with EventBridge enabled."""
        bucket = s3.Bucket(
            self,
            "FirmwareBucket",
            event_bridge_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS_MANAGED,
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        return bucket

    def _create_notification_topic(self) -> sns.Topic:
        """Create the SNS topic for operator notifications."""
        topic = sns.Topic(
            self,
            "NotificationTopic",
            topic_name="firmware-deployment-notifications",
        )

        return topic

    def _create_agent_lambda(self) -> lambda_.Function:
        """Create the Deployment Agent Lambda function with least-privilege IAM."""
        # Retrieve configuration from CDK context
        bedrock_model_id = self.node.try_get_context("bedrock_model_id") or ("us.anthropic.claude-haiku-4-5-20251001-v1:0")
        wave_timeout_minutes = self.node.try_get_context("wave_timeout_minutes") or "30"

        # CloudWatch Logs log group with 30-day retention
        log_group = logs.LogGroup(
            self,
            "AgentLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Lambda function
        agent_function = lambda_.Function(
            self,
            "DeploymentAgentFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="deployment_agent.handler.handler",
            code=lambda_.Code.from_asset(".build/lambda"),
            timeout=Duration.minutes(5),
            memory_size=512,
            log_group=log_group,
            environment={
                "FLEET_INVENTORY_TABLE": self.fleet_inventory_table.table_name,
                "DEPLOYMENT_HISTORY_TABLE": self.deployment_history_table.table_name,
                "FIRMWARE_BUCKET": self.firmware_bucket.bucket_name,
                "NOTIFICATION_TOPIC_ARN": self.notification_topic.topic_arn,
                "BEDROCK_MODEL_ID": bedrock_model_id,
                "WAVE_TIMEOUT_MINUTES": wave_timeout_minutes,
            },
        )

        # Grant least-privilege IAM permissions
        self._grant_lambda_permissions(agent_function)

        return agent_function

    def _grant_lambda_permissions(self, agent_function: lambda_.Function) -> None:
        """Grant least-privilege IAM permissions to the Lambda function."""
        # IoT permissions: CreateJob, DescribeJob, CancelJob, ListJobExecutionsForJob
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "iot:CreateJob",
                    "iot:DescribeJob",
                    "iot:CancelJob",
                    "iot:ListJobExecutionsForJob",
                ],
                resources=[
                    f"arn:aws:iot:{self.region}:{self.account}:job/*",
                    f"arn:aws:iot:{self.region}:{self.account}:thing/*",
                    f"arn:aws:iot:{self.region}:{self.account}:thinggroup/*",
                ],
            )
        )

        # STS permissions for resolving account ID in tool functions
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sts:GetCallerIdentity"],
                resources=["*"],
            )
        )

        # DynamoDB permissions: Query, GetItem, PutItem on tables and indexes
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:Query",
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                ],
                resources=[
                    self.fleet_inventory_table.table_arn,
                    f"{self.fleet_inventory_table.table_arn}/index/*",
                    self.deployment_history_table.table_arn,
                    f"{self.deployment_history_table.table_arn}/index/*",
                ],
            )
        )

        # Bedrock permissions: InvokeModel scoped to specific model
        bedrock_model_id = self.node.try_get_context("bedrock_model_id") or (
            "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        )
        # Inference profiles route requests across regions, so we need permissions
        # on the foundation model in all regions the profile may use.
        foundation_model_id = bedrock_model_id.removeprefix("us.").removeprefix("global.")
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:*::foundation-model/{foundation_model_id}",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{bedrock_model_id}",
                ],
            )
        )

        # S3 permissions: GetObject on firmware bucket
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:GetObject"],
                resources=[f"{self.firmware_bucket.bucket_arn}/*"],
            )
        )

        # SNS permissions: Publish to notification topic
        agent_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sns:Publish"],
                resources=[self.notification_topic.topic_arn],
            )
        )

    def _create_state_machine(self) -> sfn.CfnStateMachine:
        """Create the Step Functions state machine from ASL definition."""
        # Load ASL definition
        asl_path = Path(__file__).parent.parent / "step_functions" / "deployment_workflow.asl.json"
        with open(asl_path) as f:
            asl_definition = f.read()

        # Substitute placeholders
        asl_definition = asl_definition.replace("${LambdaFunctionArn}", self.agent_lambda.function_arn)
        asl_definition = asl_definition.replace("${NotificationTopicArn}", self.notification_topic.topic_arn)

        # Create IAM role for Step Functions
        sfn_role = iam.Role(
            self,
            "StateMachineRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
        )

        # Grant Step Functions permission to invoke Lambda
        sfn_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[
                    self.agent_lambda.function_arn,
                    f"{self.agent_lambda.function_arn}:*",
                ],
            )
        )

        # Grant Step Functions permission to publish to SNS
        sfn_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sns:Publish"],
                resources=[self.notification_topic.topic_arn],
            )
        )

        # Create state machine using CfnStateMachine for raw ASL
        state_machine = sfn.CfnStateMachine(
            self,
            "DeploymentStateMachine",
            definition_string=asl_definition,
            role_arn=sfn_role.role_arn,
            state_machine_name="FirmwareDeploymentOrchestrator",
        )

        return state_machine

    def _create_eventbridge_rule(self) -> events.Rule:
        """Create EventBridge rule for firmware upload events."""
        # Create the rule targeting S3 Object Created events for firmware/ prefix
        rule = events.Rule(
            self,
            "FirmwareUploadRule",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [self.firmware_bucket.bucket_name]},
                    "object": {"key": [{"prefix": "firmware/"}]},
                },
            ),
        )

        # Target: Step Functions state machine with input transformation
        # Parse device_type and target_version from filename pattern:
        # firmware/{device_type}-v{version}.bin
        rule.add_target(
            targets.SfnStateMachine(
                sfn.StateMachine.from_state_machine_arn(
                    self,
                    "ImportedStateMachine",
                    state_machine_arn=self.state_machine.attr_arn,
                ),
                input=events.RuleTargetInput.from_object(
                    {
                        "firmware_s3_url": events.EventField.from_path("$.detail.bucket.name"),
                        "s3_key": events.EventField.from_path("$.detail.object.key"),
                        "device_type": events.EventField.from_path("$.detail.object.key"),
                        "target_version": events.EventField.from_path("$.detail.object.key"),
                    }
                ),
            )
        )

        return rule

    def _create_outputs(self) -> None:
        """Create CloudFormation outputs for key resource identifiers."""
        CfnOutput(
            self,
            "StateMachineArn",
            value=self.state_machine.attr_arn,
            description="Step Functions state machine ARN",
        )

        CfnOutput(
            self,
            "FirmwareBucketName",
            value=self.firmware_bucket.bucket_name,
            description="S3 bucket name for firmware storage",
        )

        CfnOutput(
            self,
            "NotificationTopicArn",
            value=self.notification_topic.topic_arn,
            description="SNS topic ARN for deployment notifications",
        )

        CfnOutput(
            self,
            "FleetInventoryTableName",
            value=self.fleet_inventory_table.table_name,
            description="DynamoDB table name for fleet inventory",
        )

        CfnOutput(
            self,
            "DeploymentHistoryTableName",
            value=self.deployment_history_table.table_name,
            description="DynamoDB table name for deployment history",
        )
