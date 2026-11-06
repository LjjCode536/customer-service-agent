import { useMemo, useState } from 'react'
import { Alert, Card, Col, Empty, Input, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { useSearchParams } from 'react-router-dom'
import { useTraces } from '../api/queries'
import type { TraceStep } from '../api/types'

interface TraceGroup {
  trace_id: string
  steps: TraceStep[]
  totalLatency: number
  failed: number
  lastAt: string
}

function groupTraces(steps: TraceStep[]): TraceGroup[] {
  const groups = new Map<string, TraceStep[]>()
  for (const step of steps) {
    const key = step.trace_id ?? 'unassigned'
    const existing = groups.get(key) ?? []
    existing.push(step)
    groups.set(key, existing)
  }
  return [...groups.entries()]
    .map(([trace_id, items]) => {
      const ordered = [...items].sort((a, b) => a.id - b.id)
      return {
        trace_id,
        steps: ordered,
        totalLatency: ordered.reduce((sum, item) => sum + (item.latency_ms ?? 0), 0),
        failed: ordered.filter((item) => item.status === 'failed').length,
        lastAt: ordered[ordered.length - 1]?.created_at ?? '',
      }
    })
    .sort((a, b) => b.lastAt.localeCompare(a.lastAt))
}

export default function Traces() {
  const [params, setParams] = useSearchParams()
  const all = useTraces()
  const [filter, setFilter] = useState('')

  const groups = useMemo(() => groupTraces(all.data ?? []), [all.data])
  const selectedFromUrl = params.get('trace_id') ?? undefined
  const selected = selectedFromUrl ?? groups[0]?.trace_id
  const detail = useTraces(selected)

  const filteredGroups = useMemo(
    () => groups.filter((group) => group.trace_id.toLowerCase().includes(filter.toLowerCase())),
    [groups, filter],
  )

  const selectedGroup = groups.find((group) => group.trace_id === selected)
  const steps = [...(detail.data ?? selectedGroup?.steps ?? [])].sort((a, b) => a.id - b.id)
  const failed = steps.filter((step) => step.status === 'failed').length
  const totalLatency = steps.reduce((sum, step) => sum + (step.latency_ms ?? 0), 0)

  const latencyOption = {
    tooltip: { trigger: 'axis', formatter: '{b}<br/>耗时：{c} ms' },
    grid: { left: 48, right: 16, top: 24, bottom: 48 },
    xAxis: {
      type: 'category',
      data: steps.map((step) => step.node),
      axisLabel: { rotate: 30, fontSize: 11 },
    },
    yAxis: { type: 'value', name: 'ms' },
    series: [
      {
        type: 'bar',
        barMaxWidth: 36,
        data: steps.map((step) => ({
          value: step.latency_ms ?? 0,
          itemStyle: { color: step.status === 'failed' ? '#ff4d4f' : '#1677ff' },
        })),
      },
    ],
  }

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Trace Explorer
          </Typography.Title>
          <Typography.Text type="secondary">
            按 trace_id 回放每个节点的耗时、输入与输出；从 Agent Lab 可直接跳转。
          </Typography.Text>
        </div>
        <Space>
          <Input
            allowClear
            placeholder="过滤 trace_id"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            style={{ width: 260 }}
          />
          <ReloadOutlined
            style={{ cursor: 'pointer' }}
            spin={all.isFetching}
            onClick={() => {
              void all.refetch()
              void detail.refetch()
            }}
          />
        </Space>
      </Space>

      {all.isError ? <Alert type="error" showIcon title="Trace 列表加载失败" description={String(all.error)} /> : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={9}>
          <Card title={`Trace 列表（${filteredGroups.length}）`} loading={all.isLoading}>
            <Table
              rowKey="trace_id"
              size="small"
              pagination={{ pageSize: 8, size: 'small' }}
              dataSource={filteredGroups}
              rowClassName={(row) => (row.trace_id === selected ? 'ant-table-row-selected' : '')}
              onRow={(row) => ({ onClick: () => setParams({ trace_id: row.trace_id }) })}
              locale={{ emptyText: <Empty description="还没有 Trace，先到 Agent Lab 跑一个 Case" /> }}
              columns={[
                {
                  title: 'Trace',
                  dataIndex: 'trace_id',
                  render: (value: string) => (
                    <Typography.Text copyable style={{ fontSize: 12 }}>
                      {value}
                    </Typography.Text>
                  ),
                },
                { title: '节点', dataIndex: 'steps', render: (items: TraceStep[]) => items.length },
                { title: '耗时', dataIndex: 'totalLatency', render: (value: number) => `${value} ms` },
                {
                  title: '状态',
                  dataIndex: 'failed',
                  render: (value: number) => (value ? <Tag color="red">失败 {value}</Tag> : <Tag color="green">OK</Tag>),
                },
              ]}
            />
          </Card>
        </Col>

        <Col xs={24} lg={15}>
          <Card
            title={
              <Space wrap>
                节点耗时
                <Typography.Text copyable style={{ fontSize: 12 }}>
                  {selected ?? '未选择'}
                </Typography.Text>
              </Space>
            }
            loading={detail.isLoading}
            extra={
              selectedGroup ? (
                <Typography.Text type="secondary">最后活动：{selectedGroup.lastAt}</Typography.Text>
              ) : null
            }
          >
            {steps.length ? (
              <>
                <Row gutter={[16, 16]} style={{ marginBottom: 12 }}>
                  <Col span={8}>
                    <Statistic title="节点数" value={steps.length} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="总耗时" suffix="ms" value={totalLatency} />
                  </Col>
                  <Col span={8}>
                    <Statistic
                      title="失败节点"
                      value={failed}
                      styles={{ content: { color: failed ? '#cf1322' : undefined } }}
                    />
                  </Col>
                </Row>
                <ReactECharts option={latencyOption} style={{ height: 260 }} />
              </>
            ) : (
              <Empty description="该 trace_id 没有节点" />
            )}
          </Card>

          <Card title="节点明细" style={{ marginTop: 16 }} loading={detail.isLoading}>
            <Table
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={steps}
              locale={{ emptyText: <Empty description="暂无节点" /> }}
              expandable={{
                expandedRowRender: (step) => (
                  <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                    {JSON.stringify(
                      { input: step.input, output: step.output, error: step.error },
                      null,
                      2,
                    )}
                  </pre>
                ),
              }}
              columns={[
                { title: 'Node', dataIndex: 'node' },
                {
                  title: '状态',
                  dataIndex: 'status',
                  render: (value: string) => (
                    <Tag color={value === 'success' ? 'green' : 'red'}>{value}</Tag>
                  ),
                },
                {
                  title: '耗时(ms)',
                  dataIndex: 'latency_ms',
                  sorter: (a: TraceStep, b: TraceStep) => (a.latency_ms ?? 0) - (b.latency_ms ?? 0),
                },
                { title: '时间', dataIndex: 'created_at' },
                {
                  title: '错误',
                  dataIndex: 'error',
                  render: (value: string | null) => (value ? <Typography.Text type="danger">{value}</Typography.Text> : '-'),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </>
  )
}
