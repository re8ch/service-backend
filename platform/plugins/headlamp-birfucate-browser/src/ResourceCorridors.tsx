import { Box, Typography } from '@mui/material';
import React from 'react';

export type AllocationLane = {
  tenant: string;
  allocated: number;
  observed: number;
  evidence: string;
};

function amount(value: number): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 2,
    notation: Math.abs(value) >= 100000 ? 'compact' : 'standard',
  }).format(value);
}

function measured(value: number, unit: string): string {
  if (unit !== 'bytes') return `${amount(value)} ${unit}`;
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let scaled = value;
  let index = 0;
  while (Math.abs(scaled) >= 1024 && index < units.length - 1) {
    scaled /= 1024;
    index += 1;
  }
  return `${amount(scaled)} ${units[index]}`;
}

export function ResourceCorridors({
  resourceClass,
  unit,
  lanes,
  capacity,
}: {
  resourceClass: string;
  unit: string;
  lanes: AllocationLane[];
  capacity: number | null;
}) {
  const allocated = lanes.reduce((sum, lane) => sum + lane.allocated, 0);
  const remainder = capacity == null ? null : Math.max(0, capacity - allocated);
  const display = [
    ...lanes,
    ...(remainder == null
      ? []
      : [{ tenant: '未分配', allocated: remainder, observed: 0, evidence: '容量上限 − 已分配' }]),
  ];
  const height = Math.max(220, display.length * 72 + 60);
  const max = Math.max(...display.map(item => item.allocated), 1);

  return (
    <Box sx={{ overflowX: 'auto', py: 1 }}>
      <Box sx={{ minWidth: 760, display: 'grid', gridTemplateColumns: '220px 1fr', gap: 0 }}>
        <Box sx={{ alignSelf: 'center', border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 2, bgcolor: 'background.paper' }}>
          <Typography variant="subtitle1" fontWeight={700}>{resourceClass}</Typography>
          <Typography variant="body2" color="text.secondary">{capacity == null ? '可出租上限未声明' : `总量 ${measured(capacity, unit)}`}</Typography>
          <Typography variant="caption" color="text.secondary">已分配 {measured(allocated, unit)}</Typography>
        </Box>
        <svg role="img" aria-label={`${resourceClass} allocation corridors`} viewBox={`0 0 720 ${height}`} style={{ width: '100%', height }}>
          {display.map((lane, index) => {
            const y = 40 + index * 72;
            const width = 4 + Math.min(24, (lane.allocated / max) * 20);
            const unallocated = lane.tenant === '未分配';
            const color = unallocated ? '#78909c' : '#536dfe';
            return <g key={lane.tenant}>
              <path d={`M 0 ${height / 2} C 135 ${height / 2}, 190 ${y}, 300 ${y}`} fill="none" stroke={color} strokeWidth={width} opacity={unallocated ? .38 : .72}/>
              <rect x="300" y={y - 27} width="390" height="54" rx="10" fill={color} opacity={unallocated ? .22 : .92}/>
              <text x="320" y={y - 5} fill={unallocated ? 'currentColor' : 'white'} fontSize="15" fontWeight="700">{lane.tenant}</text>
              <text x="320" y={y + 15} fill={unallocated ? 'currentColor' : 'white'} fontSize="12">分配 {measured(lane.allocated, unit)} · 观测 {measured(lane.observed, unit)}</text>
              <title>{lane.evidence}</title>
            </g>;
          })}
          {!display.length && <text x="300" y={height / 2} fill="currentColor">尚无租户分配事实；容量上限也未声明</text>}
        </svg>
      </Box>
    </Box>
  );
}
