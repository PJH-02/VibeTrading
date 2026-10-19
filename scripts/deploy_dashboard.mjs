import { spawn } from "node:child_process";

// This local helper receives provisioning secrets with terminal echo disabled.
// They are forwarded through SSH stdin and never placed in process arguments.
const input = await new Promise((resolve, reject) => {
  let value = "";
  const finish = (error) => {
    process.stdin.removeListener("data", onData);
    if (process.stdin.isTTY) process.stdin.setRawMode(false);
    process.stdin.pause();
    error ? reject(error) : resolve(value);
  };
  const onData = chunk => {
    value += chunk;
    if (value.includes("\u0003") || value.length > 64000) finish(new Error("Cancelled or invalid input"));
    else if (value.includes("\n") || value.includes("\r")) finish();
  };
  if (process.stdin.isTTY) process.stdin.setRawMode(true);
  process.stderr.write("Ready for dashboard configuration on stdin (input is hidden).\n");
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", onData);
  process.stdin.resume();
});
try { JSON.parse(input); } catch { throw new Error("Invalid configuration JSON"); }
const child = spawn("ssh", ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "49.50.141.106", "python3", "/opt/vibetrading/dashboard/scripts/provision_dashboard.py"], { stdio: ["pipe", "inherit", "inherit"] });
child.stdin.end(input.trim() + "\n");
child.on("close", code => { process.exitCode = code ?? 1; });
