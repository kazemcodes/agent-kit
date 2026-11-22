<?php

/**
 * Minimal MCP client harness for FixIndex (JSON-RPC 2.0 over Streamable HTTP or SSE+POST).
 *
 * Usage:
 *   php agent-kit/mcp-client.php tools/list
 *   php agent-kit/mcp-client.php diagnose_symptom '{"query":"Xiaomi Poco F3 no mic no sound"}'
 *   php agent-kit/mcp-client.php get_device_detail '{"identifier":"xiaomi-poco-f3"}'
 *   php agent-kit/mcp-client.php sse                 # open SSE stream, print sessionId
 *
 * Environment:
 *   FIXINDEX_MCP_URL      messages endpoint (default /api/v1/mcp, or /api/v1/mcp/messages when FIXINDEX_MCP_SESSION is set)
 *   FIXINDEX_MCP_SSE_URL  SSE endpoint (default http://127.0.0.1:8000/api/v1/mcp/sse)
 *   FIXINDEX_API_KEY      Bearer token (fx_live_... with read:knowledge / contribute:agent)
 *   FIXINDEX_MCP_SESSION  sessionId obtained from the `sse` command
 */
$sessionId = getenv('FIXINDEX_MCP_SESSION') ?: '';
$envUrl = getenv('FIXINDEX_MCP_URL') ?: '';
$url = $envUrl !== ''
    ? $envUrl
    : ($sessionId !== ''
        ? 'http://127.0.0.1:8000/api/v1/mcp/messages'
        : 'http://127.0.0.1:8000/api/v1/mcp');
$sseUrl = getenv('FIXINDEX_MCP_SSE_URL') ?: 'http://127.0.0.1:8000/api/v1/mcp/sse';
$apiKey = getenv('FIXINDEX_API_KEY') ?: '';

function authHeaders(): array
{
    global $apiKey, $sessionId;
    $headers = ['Content-Type: application/json', 'Accept: application/json'];
    if ($apiKey !== '') {
        $headers[] = 'Authorization: Bearer '.$apiKey;
    }
    if ($sessionId !== '') {
        $headers[] = 'Mcp-Session-Id: '.$sessionId;
    }

    return $headers;
}

function withSession(string $url): string
{
    global $sessionId;
    if ($sessionId === '' || ! str_contains($url, '/mcp/messages')) {
        return $url;
    }

    return $url.(str_contains($url, '?') ? '&' : '?').'sessionId='.urlencode($sessionId);
}

function rpc(string $url, string $method, ?array $params = null, $id = 1): array
{
    $payload = array_filter([
        'jsonrpc' => '2.0',
        'id' => $id,
        'method' => $method,
        'params' => $params,
    ], fn ($v) => $v !== null);

    $ch = curl_init(withSession($url));
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => authHeaders(),
        CURLOPT_POSTFIELDS => json_encode($payload, JSON_UNESCAPED_UNICODE),
        CURLOPT_TIMEOUT => 60,
    ]);
    $raw = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($raw === false) {
        return ['_http' => $code, '_error' => 'transport failure'];
    }
    $decoded = json_decode($raw, true);
    if ($decoded === null) {
        return ['_http' => $code, '_raw' => $raw];
    }
    $decoded['_http'] = $code;

    return $decoded;
}

function callTool(string $url, string $tool, array $args): array
{
    return rpc($url, 'tools/call', ['name' => $tool, 'arguments' => (object) $args]);
}

if (PHP_SAPI === 'cli' && realpath($argv[0]) === realpath(__FILE__)) {
    $tool = $argv[1] ?? 'tools/list';
    if ($tool === 'sse') {
        $captured = '';
        $ch = curl_init($sseUrl);
        curl_setopt_array($ch, [
            CURLOPT_HTTPHEADER => authHeaders(),
            CURLOPT_TIMEOUT => 4,
            CURLOPT_WRITEFUNCTION => function ($ch, $chunk) use (&$captured) {
                $captured .= $chunk;

                return strlen($chunk);
            },
        ]);
        curl_exec($ch);
        curl_close($ch);
        if (preg_match('/sessionId=([a-f0-9-]+)/', $captured, $m)) {
            echo $m[1].PHP_EOL;
        } else {
            fwrite(STDERR, 'No endpoint event received. Response: '.substr($captured, 0, 300).PHP_EOL);
            exit(1);
        }
        exit(0);
    }
    if ($tool === 'tools/list') {
        $res = rpc($url, 'tools/list');
        foreach ($res['result']['tools'] ?? [] as $t) {
            echo $t['name'].' — '.$t['description'].PHP_EOL;
        }
        exit(0);
    }
    if ($tool === 'initialize') {
        print_r(rpc($url, 'initialize', ['protocolVersion' => '2024-11-05', 'capabilities' => new stdClass, 'clientInfo' => ['name' => 'fixindex-test', 'version' => '1.0']]));
        exit(0);
    }
    $args = json_decode($argv[2] ?? '{}', true) ?? [];
    $res = callTool($url, $tool, $args);
    echo 'HTTP '.($res['_http'] ?? '?').PHP_EOL;
    if (isset($res['result']['content'][0]['text'])) {
        echo $res['result']['content'][0]['text'].PHP_EOL;
    } else {
        echo json_encode($res, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE).PHP_EOL;
    }
}
