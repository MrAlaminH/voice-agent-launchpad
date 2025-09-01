# LiveKit Agent Telephony Integration Setup Guide

This guide explains how to set up and use the telephony integration for your LiveKit voice AI agent, enabling it to make and receive phone calls using Twilio SIP.

## 🚀 Features

- **Inbound Calls**: Receive phone calls and route them to your AI agent
- **Outbound Calls**: Make phone calls from your AI agent
- **Call Management**: Track call status, duration, and metadata
- **Recording**: Automatic call recording with S3/R2 storage
- **Webhooks**: Real-time notifications for call events
- **Professional Integration**: Seamless integration with existing agent infrastructure

## 📋 Prerequisites

1. **LiveKit Server**: Self-hosted or LiveKit Cloud
2. **Twilio Account**: For SIP trunk configuration
3. **S3/R2 Storage**: For call recordings (optional)
4. **Public Webhook Endpoint**: For receiving call notifications

## 🔧 Configuration

### 1. Environment Variables

Copy `env.example` to `.env.local` and configure the following variables:

```bash
# LiveKit Configuration
LIVEKIT_URL=wss://your-livekit-server.com
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret

# Enable Telephony
ENABLE_TELEPHONY=1

# Twilio SIP Configuration
TWILIO_SIP_TRUNK_ID=your-twilio-sip-trunk-id
TWILIO_OUTBOUND_TRUNK_ID=your-twilio-outbound-trunk-id
TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token

# Webhook Configuration
CALL_WEBHOOK_URL=https://your-webhook-endpoint.com/webhook/call
WEBHOOK_SERVER_PORT=8000
WEBHOOK_SERVER_HOST=0.0.0.0
```

### 2. Twilio SIP Trunk Setup

#### Step 1: Create SIP Trunk in Twilio

1. Go to Twilio Console → Voice → SIP Trunks
2. Create a new SIP trunk
3. Note the SIP trunk ID for configuration

#### Step 2: Configure SIP Trunk

- **Inbound Configuration**:

  - Set the webhook URL to: `https://your-domain.com/webhook/twilio/inbound`
  - HTTP Method: POST
  - Event: Incoming Call

- **Outbound Configuration**:
  - Set the SIP URI to your LiveKit server
  - Configure authentication if required

#### Step 3: LiveKit SIP Configuration

1. Install LiveKit SIP service
2. Configure SIP trunks in LiveKit
3. Set up dispatch rules for call routing

### 3. Webhook Server Setup

The webhook server handles inbound call notifications and routes them to your agent.

#### Start Webhook Server:

```bash
# Install dependencies
uv sync

# Start webhook server
uv run python src/main/webhook_server.py
```

#### Webhook Endpoints:

- `POST /webhook/twilio/inbound` - Twilio inbound calls
- `POST /webhook/generic/inbound` - Generic inbound calls
- `POST /webhook/call/completion` - Call completion events
- `POST /webhook/agent/status` - Agent status updates

## 🎯 Usage

### Inbound Calls

When someone calls your Twilio phone number:

1. **Webhook Triggered**: Twilio sends webhook to your server
2. **Room Creation**: System creates a LiveKit room
3. **Agent Session**: AI agent joins the room
4. **SIP Connection**: Caller is connected via SIP
5. **Conversation**: AI agent handles the conversation

### Outbound Calls

Your AI agent can make outbound calls using the telephony tools:

```python
# Example: Agent making an appointment reminder call
result = await agent.make_outbound_call(
    phone_number="+1234567890",
    purpose="appointment reminder",
    agent_instructions="Remind the patient about their appointment tomorrow"
)
```

### Agent Tools

The agent has access to these telephony tools:

- `make_outbound_call` - Make outbound calls
- `get_call_status` - Check call status
- `end_call` - End active calls
- `list_active_calls` - List all active calls
- `validate_phone_number` - Validate phone numbers

## 📊 Call Tracking

### Call Metadata

Each call is tracked with comprehensive metadata:

```json
{
  "call_id": "inbound_20241201_143022_1234567890",
  "direction": "inbound",
  "phone_number": "+1234567890",
  "room_name": "agent_call_20241201_143022_1234567890",
  "status": "connected",
  "start_time": "2024-12-01T14:30:22Z",
  "duration_seconds": 180,
  "recording_url": "https://your-bucket.com/recording.mp4",
  "transcript": [...],
  "metadata": {...}
}
```

### Webhook Events

The system sends webhooks for these events:

- `call_started` - When a call begins
- `call_ended` - When a call ends
- `call_initiated` - When outbound call is initiated

## 🔒 Security Considerations

1. **Webhook Authentication**: Implement webhook signature verification
2. **SIP Security**: Use secure SIP trunks with authentication
3. **API Keys**: Keep LiveKit and Twilio credentials secure
4. **HTTPS**: Use HTTPS for all webhook endpoints

## 🚀 Production Deployment

### 1. Webhook Server Deployment

Deploy the webhook server to a production environment:

```bash
# Using Docker
docker build -t livekit-agent-webhook .
docker run -p 8000:8000 livekit-agent-webhook

# Using systemd service
sudo systemctl enable livekit-agent-webhook
sudo systemctl start livekit-agent-webhook
```

### 2. Load Balancing

For high availability, deploy multiple webhook server instances behind a load balancer.

### 3. Monitoring

Set up monitoring for:

- Webhook server health
- Call success rates
- Agent response times
- Error rates

## 🧪 Testing

### Test Inbound Calls

1. Call your Twilio phone number
2. Verify webhook is received
3. Check agent joins the room
4. Test conversation flow

### Test Outbound Calls

1. Use agent console to make outbound call
2. Verify call is initiated
3. Check call status updates
4. Test call completion

### Test Webhooks

```bash
# Test webhook endpoint
curl -X POST https://your-domain.com/webhook/twilio/inbound \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "CallSid=test123&From=+1234567890&To=+0987654321&CallStatus=ringing"
```

## 🔧 Troubleshooting

### Common Issues

1. **Webhook Not Received**

   - Check webhook URL configuration
   - Verify server is accessible
   - Check firewall settings

2. **SIP Connection Failed**

   - Verify SIP trunk configuration
   - Check LiveKit SIP service
   - Validate authentication credentials

3. **Agent Not Joining Room**
   - Check LiveKit API credentials
   - Verify room creation
   - Check agent worker status

### Debug Logging

Enable debug logging to troubleshoot issues:

```bash
export LOG_LEVEL=DEBUG
```

## 📈 Scaling

### Horizontal Scaling

1. **Multiple Agent Workers**: Deploy multiple agent instances
2. **Load Balancing**: Use load balancer for webhook server
3. **Database**: Use database for call tracking (optional)

### Performance Optimization

1. **Connection Pooling**: Reuse LiveKit API connections
2. **Async Processing**: Use background tasks for webhooks
3. **Caching**: Cache frequently accessed data

## 🔮 Future Enhancements

1. **Call Queuing**: Queue calls when all agents are busy
2. **Agent Skills**: Route calls based on agent capabilities
3. **Call Transfer**: Transfer calls between agents
4. **Analytics Dashboard**: Real-time call analytics
5. **Multi-language Support**: Support for multiple languages
6. **Voice Biometrics**: Speaker identification and verification

## 📞 Support

For issues and questions:

1. Check the troubleshooting section
2. Review LiveKit documentation
3. Check Twilio SIP documentation
4. Open an issue in the repository

## 📄 License

This telephony integration is part of the LiveKit Agent Starter project and is licensed under the MIT License.
