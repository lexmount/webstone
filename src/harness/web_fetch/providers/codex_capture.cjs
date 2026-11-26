const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const { parseEnv } = require('node:util');
const { spawn, execFile } = require('node:child_process');
const { createHash } = require('node:crypto');

const [launchFolder, environmentOrMode, invocationOrUrl, assignedUrl] = process.argv.slice(2);
const localStandalone = environmentOrMode === '--moli-url';
const envFile = localStandalone ? null : environmentOrMode;
const invokeFlag = localStandalone ? null : invocationOrUrl;
const invokeUrl = localStandalone ? invocationOrUrl : assignedUrl;
const standalone = localStandalone || invokeFlag === '--invoke-url';
if (invokeFlag && !standalone) throw new Error('Unknown capture invocation');
const readContract = directory => JSON.parse(fs.readFileSync(path.join(directory, 'contract.json')));
const bindingPath = envFile && path.join(path.dirname(envFile), 'runs', '.lexmount-active-run.json');
// Desktop may retain an earlier dispatch argument. Treat the experiment
// directory name as a legacy selector, without opening that directory.
const boundDispatch = launchFolder === '--bound-dispatch' ||
    /^lexmount-api-530-/.test(path.basename(launchFolder));
const folder = boundDispatch
    ? JSON.parse(fs.readFileSync(bindingPath, 'utf8')).run
    : launchFolder;
if (boundDispatch && (typeof folder !== 'string' || !path.isAbsolute(folder))) {
    throw new Error('Invalid capture run path');
}
const contract = readContract(folder);
if (boundDispatch && contract.mode !== 'lexmount-dispatch') {
    throw new Error('Invalid Lexmount dispatch run');
}
const native = contract.mode === 'single' || contract.mode === 'same-page';
const official = contract.mode === 'lexmount-cli';
const localMoli = contract.mode === 'moli-cli';
if (localStandalone !== localMoli) throw new Error('Local Moli requires its credential-free invocation');
// Terminating the transport must also stop its detached CLI process groups.
process.once('SIGTERM', () => process.exit(143));
process.once('SIGINT', () => process.exit(130));
const tool = native ? {
    name: 'save',
    description: 'Save the native open response for the scorer.',
    annotations: { readOnlyHint: true, destructiveHint: false },
    inputSchema: {
        type: 'object',
        properties: { response: { type: 'string' } },
        required: ['response'],
        additionalProperties: false,
    },
} : {
    name: 'lexmount_fetch',
    description: 'Fetch the assigned URL once with Lexmount Extract Auto and save the response for scoring.',
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    inputSchema: {
        type: 'object',
        properties: { url: { type: 'string' } },
        required: ['url'],
        additionalProperties: false,
    },
};

function reply(id, result) {
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, result }) + '\n');
}

function protocolError(id, code, message) {
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: id ?? null,
        error: { code, message } }) + '\n');
}

function receipt(id, saved, active = contract) {
    if (standalone) {
        process.stdout.write(JSON.stringify({ status: saved ? 'saved' : 'failed',
            path: active.answerPath || active.answer || path.join(folder, 'response.bin') }) + '\n');
        return;
    }
    if (['lexmount-cli', 'lexmount-api', 'moli-cli'].includes(active.mode)) {
        const value = { status: saved ? 'saved' : 'failed',
            path: active.answerPath || active.answer || path.join(folder, 'response.bin') };
        return reply(id, { content: [{ type: 'text', text: JSON.stringify(value) }] });
    }
    reply(id, {
        content: [{ type: 'text', text: saved ? 'saved' : 'failed' }],
        ...(saved ? {} : { isError: true }),
    });
}

function log(event, details = {}, active = contract) {
    if (!active.events) return;
    fs.appendFileSync(active.events, JSON.stringify({
        event, timestamp: new Date().toISOString(), ...details,
    }) + '\n', { flush: true });
}

async function runningGroup(pid) {
    const states = await new Promise((resolve, reject) => {
        execFile('ps', ['-axo', 'pgid=,stat='], { timeout: 500, killSignal: 'SIGKILL' },
            (error, stdout) => error ? reject(error) : resolve(stdout));
    });
    return states.split('\n').some(line => {
        const [group, state] = line.trim().split(/\s+/);
        return Number(group) === pid && state && !state.startsWith('Z');
    });
}

async function finishGroup(pid, stop, event) {
    if (!pid || !stop()) return true;
    const deadline = performance.now() + 1000;
    try {
        do {
            if (!await runningGroup(pid)) return true;
            stop();
            await new Promise(resolve => setTimeout(resolve, 20));
        } while (performance.now() < deadline);
    } catch (error) {
        event('cleanup_error', { code: error.code ?? error.name });
    }
    return false;
}

async function extractWithCli(id, active = contract, work = folder) {
    const contract = active;
    const folder = work;
    const event = (name, details = {}) => log(name, details, active);
    let stdout, stderr, temporary;
    let saved = false;
    try {
        event('tool_called');
        fs.writeFileSync(path.join(folder, 'called'), '', { flag: 'wx', flush: true });
        const isMoli = contract.mode === 'moli-cli';
        const env = isMoli ? {} : parseEnv(fs.readFileSync(envFile, 'utf8'));
        const { LEXMOUNT_API_KEY, LEXMOUNT_PROJECT_ID } = env;
        if (!isMoli && (!LEXMOUNT_API_KEY || !LEXMOUNT_PROJECT_ID)) throw new Error('Missing credentials');
        if ((isMoli || standalone) && createHash('sha256').update(fs.readFileSync(contract.executable)).digest('hex') !== contract.binary_sha256) {
            throw new Error('Executable changed');
        }
        temporary = contract.answer + '.partial';
        stdout = fs.openSync(temporary, 'wx');
        stderr = fs.openSync(contract.stderr, 'a');
        const started = performance.now();
        event('client_launch');
        const args = isMoli ? ['fetch', '--dump', 'markdown', '--log-level', 'debug', contract.url]
            : ['extract', '--url', contract.url];
        const localEnvironment = Object.fromEntries([
            'HOME', 'PATH', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'CURL_CA_BUNDLE',
            'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
        ].filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]]));
        const child = spawn(contract.executable, args, {
            cwd: folder,
            env: isMoli ? localEnvironment : {
                HOME: folder,
                PATH: process.env.PATH,
                LEXMOUNT_API_KEY,
                LEXMOUNT_PROJECT_ID,
                LEXMOUNT_WEBFETCH_BASE_URL: contract.endpoint,
            },
            stdio: ['ignore', stdout, stderr],
            detached: true,
        });
        const stop = () => {
            if (!child.pid) return false;
            try {
                process.kill(-child.pid, 'SIGKILL');
                event('client_stopped', { process_group: child.pid });
                return true;
            } catch (error) {
                if (error.code !== 'ESRCH') event('cleanup_error', { code: error.code });
                return error.code !== 'ESRCH';
            }
        };
        const timer = setTimeout(stop, Math.max(0, contract.deadline - Date.now()));
        process.once('exit', stop);
        const exit = await new Promise(resolve => {
            child.on('error', error => resolve({ error: error.code }));
            child.on('close', (code, signal) => resolve({ code, signal }));
            child.on('spawn', () => event('client_started', { process_group: child.pid }));
        });
        clearTimeout(timer);
        exit.cleanup_complete = await finishGroup(child.pid, stop, event);
        event('group_cleanup_finished', { process_group: child.pid, complete: exit.cleanup_complete });
        if (!exit.cleanup_complete) throw new Error('Process group cleanup incomplete');
        process.removeListener('exit', stop);
        if (!isMoli) {
            const diagnostics = fs.readFileSync(contract.stderr, 'utf8')
                .replaceAll(LEXMOUNT_API_KEY, '[redacted]')
                .replaceAll(LEXMOUNT_PROJECT_ID, '[redacted]');
            fs.writeFileSync(contract.stderr, diagnostics, { flush: true });
        }
        const output = fs.readFileSync(temporary);
        event('client_finished', { ...exit, elapsed_ms: performance.now() - started });
        if (exit.code === 0 || isMoli || standalone) {
            fs.fsyncSync(stdout);
            fs.renameSync(temporary, contract.answer);
            const engine = output.toString('utf8', 0, 8192).match(/^- \*\*Engine:\*\* ([\w-]+)$/m);
            event('answer_saved', {
                bytes: output.length,
                sha256: createHash('sha256').update(output).digest('hex'),
                engine: engine && engine[1] !== '-' ? engine[1] : null,
            });
            saved = exit.code === 0 && exit.cleanup_complete;
            if (isMoli || standalone) fs.writeFileSync(path.join(folder, 'status.json'), JSON.stringify({
                ...exit, bytes: output.length, elapsed_ms: performance.now() - started,
                sha256: createHash('sha256').update(output).digest('hex'),
            }), { flag: 'wx', flush: true });
        }
    } catch (error) {
        event('tool_error', { code: error.code ?? error.name });
    } finally {
        if (stdout !== undefined) fs.closeSync(stdout);
        if (stderr !== undefined) fs.closeSync(stderr);
        event('receipt_sent', { status: saved ? 'saved' : 'failed' });
        receipt(id, saved, active);
    }
}

function dispatchContract(url) {
    if (typeof url !== 'string') throw new Error('Invalid URL');
    const key = createHash('sha256').update(url).digest('hex');
    const reservation = JSON.parse(fs.readFileSync(path.join(folder, 'dispatch', key + '.json')));
    if (reservation.url !== url || !/^[a-f0-9]{32}$/.test(reservation.slot) ||
        !Number.isSafeInteger(reservation.deadline)) {
        throw new Error('Invalid reservation');
    }
    const slot = reservation.slot;
    const privateFolder = path.join(folder, 'private_artifacts', slot);
    return {
        work: path.join(privateFolder, 'work'),
        active: {
            mode: 'lexmount-cli', url, deadline: reservation.deadline,
            executable: contract.executable, endpoint: contract.endpoint,
            answer: path.join(folder, slot, 'answer.md'), answerPath: slot + '/answer.md',
            stderr: path.join(privateFolder, 'cli.stderr.log'),
            events: path.join(folder, 'private_artifacts', 'live-logs', slot, 'events.jsonl'),
        },
    };
}

let savedCount = 0;
const seenLines = new Set();

function saveNative(id, response) {
    if (typeof response !== 'string' || (savedCount && contract.mode === 'single')) {
        return receipt(id, false);
    }
    const data = JSON.parse(response);
    fs.writeFileSync(path.join(folder, `open-${savedCount + 1}.json`), response, { flag: 'wx' });
    savedCount += 1;
    if (contract.mode === 'single') return receipt(id, true);

    // The native text response starts with one tool-generated header. Lines
    // from the page are numbered, so only use the unnumbered header for refs.
    const body = typeof data === 'string' ? data : '';
    const index = body.search(/^L\d+(?:@P\d+(?:-\d+)?)?:/m);
    const header = index < 0 ? body : body.slice(0, index);
    const ref = header.match(/^cite(turn\d+view\d+) .*?; Source: open\(/m)?.[1];
    const total = header.match(/; Total lines: (\d+)/)?.[1];
    const numbers = [...body.matchAll(/^L(\d+)(?:@P\d+(?:-\d+)?)?:/gm)]
        .map(match => Number(match[1]));
    const previous = seenLines.size;
    for (const number of numbers) seenLines.add(number);
    const result = {
        status: 'saved',
        ref_id: ref || null,
        total_lines: total === undefined ? null : Number(total),
        first_line: numbers[0] ?? null,
        last_line: numbers.at(-1) ?? null,
        new_lines: seenLines.size - previous,
        error: /^(?:Error:\s*)?(?:Failed to fetch(?:\s+https?:\/\/\S+(?::\s.*)?)?\s*$|Internal Error(?:\s*\(\))?\s*$)/m.test(header),
    };
    return reply(id, { content: [{ type: 'text', text: JSON.stringify(result) }] });
}

async function handle({ method, id, params }, active = contract, work = folder) {
    const contract = active;
    const folder = work;
    const dispatch = contract.mode === 'lexmount-dispatch';
    const event = (name, details = {}) => log(name, details, active);
    if (dispatch) {
        fs.mkdirSync(path.join(folder, 'private_artifacts'), { recursive: true });
        fs.appendFileSync(path.join(folder, 'private_artifacts', 'transport.events.jsonl'),
            JSON.stringify({ timestamp: new Date().toISOString(), method, id,
                protocolVersion: params?.protocolVersion }) + '\n', { flush: true });
    }
    if (id === undefined) return;
    if (method === 'initialize') {
        if (typeof params?.protocolVersion !== 'string') {
            return protocolError(id, -32602, 'Invalid initialize parameters');
        }
        reply(id, {
            protocolVersion: params.protocolVersion,
            serverInfo: { name: native ? 'capture' : 'lexmount', version: '1' },
            capabilities: { tools: {} },
        });
    } else if (method === 'tools/list') {
        reply(id, { tools: [tool] });
    } else if (method === 'tools/call') {
        if (dispatch) {
            try {
                if (params?.name !== tool.name || Object.keys(params.arguments ?? {}).join() !== 'url') {
                    throw new Error('Invalid arguments');
                }
                const { active, work } = dispatchContract(params.arguments.url);
                if (Date.now() >= active.deadline) {
                    log('tool_rejected', { reason: 'deadline' }, active);
                    return receipt(id, false, active);
                }
                if (active.mode === 'moli-cli' || active.mode === 'lexmount-cli') {
                    return await extractWithCli(id, active, work);
                }
                return await handle({ method, id, params }, active, work);
            } catch {
                return reply(id, { isError: true, content: [{ type: 'text', text: 'No active reservation' }] });
            }
        }
        if (native) {
            if (params?.name !== tool.name || Date.now() >= contract.deadline) {
                return receipt(id, false);
            }
            try {
                return saveNative(id, params.arguments?.response);
            } catch {
                return receipt(id, false);
            }
        }
        if (params?.name !== tool.name || params.arguments?.url !== contract.url ||
            Object.keys(params.arguments).length !== 1 || Date.now() >= contract.deadline) {
            return receipt(id, false, active);
        }
        if (official || localMoli) return extractWithCli(id);
        try {
            event('tool_called');
            fs.writeFileSync(path.join(folder, 'called'), '', { flag: 'wx' });
        } catch {
            return receipt(id, false, active);
        }
        try {
            const env = parseEnv(fs.readFileSync(envFile, 'utf8'));
            event('api_started');
            const started = performance.now();
            const response = await fetch((contract.endpoint || 'https://api.lexmount.com') + '/v1/extract', {
                method: 'POST',
                redirect: 'error',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': env.LEXMOUNT_API_KEY,
                    'X-Project-Id': env.LEXMOUNT_PROJECT_ID,
                },
                body: JSON.stringify({
                    extract: { url: contract.url, options: { engine_preference: 'auto' } },
                }),
                signal: AbortSignal.timeout(Math.max(1, contract.deadline - Date.now())),
            });
            const bytes = Buffer.from(await response.arrayBuffer());
            fs.writeFileSync(path.join(folder, 'response.bin'), bytes, { flag: 'wx', flush: true });
            fs.writeFileSync(path.join(folder, 'status.json'), JSON.stringify({
                status: response.status, bytes: bytes.length,
                sha256: createHash('sha256').update(bytes).digest('hex'),
                elapsed_ms: performance.now() - started,
                headers: { 'content-type': response.headers.get('content-type'),
                    'x-request-id': response.headers.get('x-request-id') },
            }), { flag: 'wx', flush: true });
            event('api_finished', { status: response.status, bytes: bytes.length,
                elapsed_ms: performance.now() - started });
            event('receipt_sent', { status: 'saved' });
            receipt(id, true, active);
        } catch (error) {
            const code = error.cause?.code || error.name;
            fs.writeFileSync(path.join(folder, 'error.json'), JSON.stringify({
                code: typeof code === 'string' && /^[A-Za-z0-9_]+$/.test(code) ? code : 'Error',
            }));
            event('receipt_sent', { status: 'failed' });
            receipt(id, false, active);
        }
    } else if (method === 'ping') {
        reply(id, {});
    } else {
        // New clients probe server/discover before falling back to initialize.
        // An empty successful response prevents that protocol fallback.
        process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id,
            error: { code: -32601, message: 'Method not found' } }) + '\n');
    }
}

if (standalone) {
    void handle({ method: 'tools/call', id: 1,
        params: { name: tool.name, arguments: { url: invokeUrl } } }).catch(() => receipt(1, false));
} else {
    readline.createInterface({ input: process.stdin }).on('line', line => {
        let request;
        try {
            request = JSON.parse(line);
        } catch {
            return protocolError(null, -32700, 'Invalid JSON');
        }
        if (!request || typeof request !== 'object' || typeof request.method !== 'string') {
            return protocolError(request?.id, -32600, 'Invalid request');
        }
        void handle(request).catch(() => protocolError(request.id, -32603, 'Request failed'));
    });
}
