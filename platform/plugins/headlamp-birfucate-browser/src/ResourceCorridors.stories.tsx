import { Meta, StoryFn } from '@storybook/react';
import React from 'react';
import { ResourceCorridors } from './ResourceCorridors';

export default {
  title: 'Bifurcate/Global resource allocation',
  component: ResourceCorridors,
  parameters: { layout: 'padded' },
} as Meta;

export const MemoryWithTenantAndUnallocated: StoryFn = () => <ResourceCorridors
  resourceClass="cluster.memory"
  unit="bytes"
  capacity={128 * 1024 ** 3}
  lanes={[
    { tenant: 're8ch', allocated: 28 * 1024 ** 3, observed: 24 * 1024 ** 3, evidence: '12 active Pods' },
    { tenant: 'commerce', allocated: 16 * 1024 ** 3, observed: 11 * 1024 ** 3, evidence: '7 active Pods' },
    { tenant: 'research', allocated: 8 * 1024 ** 3, observed: 6 * 1024 ** 3, evidence: '4 active Pods' },
  ]}
/>;

export const UnknownNetworkCeiling: StoryFn = () => <ResourceCorridors
  resourceClass="cluster.network-egress"
  unit="bytes"
  capacity={null}
  lanes={[
    { tenant: 're8ch', allocated: 482_344_960, observed: 482_344_960, evidence: 'network_egress_byte' },
    { tenant: 'commerce', allocated: 128_450_560, observed: 128_450_560, evidence: 'network_egress_byte' },
  ]}
/>;
