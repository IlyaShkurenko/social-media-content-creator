import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
export default function Scene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return <AbsoluteFill style={{background: '#FFFCEE', alignItems:'center', justifyContent:'center', fontSize:72}}>
    <div style={{transform:`translateY(${Math.sin(frame/fps)*30}px)`}}>Motion worker</div>
  </AbsoluteFill>;
}
