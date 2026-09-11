// Trusted worker. All generated code is bundled inside the OS sandbox.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';

const stage = process.argv[2];
const port = Number(process.argv[3]);
const debugPort = Number(process.argv[4]);
const root = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.join(stage, 'project');
const project = JSON.parse(fs.readFileSync(path.join(projectDir, 'project.json'), 'utf8'));
console.log('Worker initialized');
// Geometry and duration belong to the trusted contract, not to generated source.
fs.writeFileSync(path.join(projectDir, 'index.tsx'), `
import React from 'react';
import {registerRoot, Composition, AbsoluteFill, Audio, staticFile} from 'remotion';
import Scene from './Scene';
import project from './project.json';
const assets = Object.fromEntries(Object.entries(project.assets).map(([id, p]) => [id, staticFile(p.replace(/^public\\//, ''))]));
const Film = () => <AbsoluteFill><Scene project={project} assets={assets}/>
{project.audio.asset_ids.map((id) => <Audio key={id} src={assets[id]}
  volume={project.audio.volumes?.[id] ?? 1} />)}</AbsoluteFill>;
registerRoot(() => <Composition id={project.composition_id} component={Film}
  width={project.width} height={project.height} fps={project.fps}
  durationInFrames={Math.round(project.duration_seconds * project.fps)}/>);
`);
const serveUrl = await bundle({entryPoint: path.join(projectDir, 'index.tsx'),
  rootDir: root, publicDir: path.join(projectDir, 'public'),
  outDir: path.join(stage, 'bundle'), enableCaching: false,
  webpackOverride: (config) => ({...config, resolve: {...config.resolve,
    modules: [path.join(root, 'node_modules'), 'node_modules']}})});
const binary = path.join(root, 'node_modules/.remotion/chrome-headless-shell/mac-arm64/chrome-headless-shell-mac-arm64/chrome-headless-shell');
const browserExecutable = path.join(stage, 'browser.sh');
const shellQuotedBinary = "'" + binary.replaceAll("'", "'\\''") + "'";
fs.writeFileSync(browserExecutable, `#!/bin/sh\nexec ${shellQuotedBinary} "$@" --remote-debugging-port=${debugPort}\n`, {mode:0o700});
const options = {serveUrl, id: project.composition_id, browserExecutable, port,
  chromiumOptions: {gl: 'swangle'}, logLevel: 'warn'};
const composition = await selectComposition(options);
await renderMedia({...options, composition, codec: 'h264',
  outputLocation: path.join(stage, 'video.mp4'), concurrency: 2,
  crf: 18, audioCodec: 'aac', pixelFormat: 'yuv420p'});
console.log('Render complete');
