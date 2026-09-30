import ReactECharts from 'echarts-for-react';

// ECharts option objects are built dynamically per view; the library's
// EChartsOption type is far too rigid for that, so we pass `any` through.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type EChartOption = any;

/** Thin ECharts wrapper (dark theme, fixed height, full width). */
export function Chart({
  option,
  height = 320,
  onEvents,
}: {
  option: EChartOption;
  height?: number;
  onEvents?: Record<string, (p: unknown) => void>;
}) {
  return (
    <ReactECharts
      option={option}
      theme="dark"
      notMerge
      lazyUpdate
      style={{ height, width: '100%' }}
      onEvents={onEvents}
    />
  );
}

/** Base grid/axis options shared by most charts (dark theme). */
export const baseAxis = {
  axisLine: { lineStyle: { color: '#555' } },
  axisLabel: { color: '#bbb' },
  splitLine: { lineStyle: { color: '#2a2a2a' } },
};

export const tooltipBase = { trigger: 'axis' as const };
