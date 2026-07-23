#!/usr/bin/env python3
"""CDK app entry point for the IoT Firmware Orchestration Agent."""

import aws_cdk as cdk
from stacks.firmware_agent_stack import FirmwareAgentStack

app = cdk.App()
FirmwareAgentStack(app, "FirmwareAgentStack")
app.synth()
