-- Initialize databases for all services

-- Create database for Langfuse
CREATE DATABASE langfuse;

-- Create database for LiteLLM
CREATE DATABASE litellm;

-- Create database for Open WebUI
CREATE DATABASE openwebui;

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE langfuse TO llmops;
GRANT ALL PRIVILEGES ON DATABASE litellm TO llmops;
GRANT ALL PRIVILEGES ON DATABASE openwebui TO llmops;

-- Connect to langfuse database and set up schema
\c langfuse;
GRANT ALL ON SCHEMA public TO llmops;

-- Connect to litellm database and set up schema
\c litellm;
GRANT ALL ON SCHEMA public TO llmops;

-- Connect to openwebui database and set up schema
\c openwebui;
GRANT ALL ON SCHEMA public TO llmops;
