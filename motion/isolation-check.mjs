import fs from 'node:fs';
import net from 'node:net';
const [sentinel, forbiddenWrite] = process.argv.slice(2);
const results = {};
try {fs.readFileSync(sentinel); results.private_read_denied=false;} catch {results.private_read_denied=true;}
try {fs.writeFileSync(forbiddenWrite, 'unexpected'); results.outside_write_denied=false;} catch {results.outside_write_denied=true;}
results.secret_environment_removed = !process.env.MOTION_TEST_SECRET;
results.external_network_denied = await new Promise(resolve => {
  const socket = net.connect({host:'1.1.1.1', port:443});
  socket.setTimeout(2000);
  socket.on('connect', () => {socket.destroy(); resolve(false);});
  socket.on('error', error => {socket.destroy(); resolve(error.code === 'EPERM' || error.code === 'EACCES');});
  socket.on('timeout', () => {socket.destroy(); resolve(false);});
});
console.log(JSON.stringify(results));
if (!Object.values(results).every(Boolean)) process.exitCode=1;
