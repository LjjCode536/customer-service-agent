import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  Row,
  Select,
  Space,
  Tag,
  Timeline,
  Typography,
  message as toast,
} from 'antd'
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { API_BASE, securityHeaders } from '../api/client'
import { queryKeys, useMockCases, useRunChat, useRunMockCase, useTask } from '../api/queries'
import { useRuntime } from '../store/useRuntime'
import type { ChatResponse, TaskEvent } from '../api/types'

const eventMeta: Record<string, { color: string; label: string }> = {
  task_created: { color: 'blue', label: '任务已创建' },
  workflow_start: { color: 'geekblue', label: 'Workflow 启动' },
  node_start: { color: 'processing', label: '节点开始' },
  node_completed: { color: 'green', label: '节点完成' },
  node_failed: { color: 'red', label: '节点失败' },
  node_paused: { color: 'orange', label: '节点暂停' },
  human_review_required: { color: 'gold', label: '等待人工审核' },
  ticket_created: { color: 'cyan', label: '工单已创建' },
  refund_submitted: { color: 'green', label: '退款已提交' },
  task_completed: { color: 'green', label: '任务完成' },
  task_failed: { color: 'red', label: '任务失败' },
  direct_result: { color: 'purple', label: '直接返回' },
}

function eventLabel(eventType: string) {
  return eventMeta[eventType]?.label ?? eventType
}

function eventColor(eventType: string) {
  return eventMeta[eventType]?.color ?? 'gray'
}

function TimelineEvent({ event, elapsed, delta }: { event: TaskEvent; elapsed: number; delta: number }) {
  return (
    <Space orientation="vertical" size={2} style={{ width: '100%' }}>
      <Space wrap size={6}>
        <Tag color={eventColor(event.event_type)}>{eventLabel(event.event_type)}</Tag>
        {event.node ? <Tag>{event.node}</Tag> : null}
        <Typography.Text type="secondary">
          +{elapsed}ms（本步 {delta}ms）
        </Typography.Text>
      </Space>
      {event.error ? <Typography.Text type="danger">{event.error}</Typography.Text> : null}
      {event.output !== undefined ? (
        <Typography.Text type="secondary" ellipsis style={{ maxWidth: '100%' }}>
          {JSON.stringify(event.output)}
        </Typography.Text>
      ) : null}
    </Space>
  )
}

export default function AgentLab() {
  const cases = useMockCases()
  const runMock = useRunMockCase()
  const runChat = useRunChat()
  const queryClient = useQueryClient()
  const { user, tenant, role, mode, setUser } = useRuntime()

  const [caseKey, setCaseKey] = useState('logistics_stale')
  const [text, setText] = useState('订单 O10001 物流两天没更新，到底怎么回事？')
  const [result, setResult] = useState<ChatResponse>()
  const [events, setEvents] = useState<TaskEvent[]>([])
  const [taskId, setTaskId] = useState<string>()
  const [running, setRunning] = useState(false)
  const [streamError, setStreamError] = useState<string>()
  const abortRef = useRef<AbortController | undefined>(undefined)

  const task = useTask(taskId)
  const selectedCase = cases.data?.find((item) => item.case_key === caseKey)

  const timeline = useMemo(() => {
    let previous = 0
    return events.map((event) => {
      const elapsed = Math.round(event.received_at)
      const delta = Math.max(0, elapsed - previous)
      previous = elapsed
      return { event, elapsed, delta }
    })
  }, [events])

  useEffect(() => () => abortRef.current?.abort(), [])

  useEffect(() => {
    if (!cases.data?.length) return
    const current = cases.data.find((item) => item.case_key === caseKey)
    if (current && !text) {
      setText(current.message)
      setUser(current.user_id)
    }
  }, [cases.data, caseKey, text, setUser])

  function choose(key: string) {
    setCaseKey(key)
    const selected = cases.data?.find((item) => item.case_key === key)
    if (selected) {
      setText(selected.message)
      setUser(selected.user_id)
    }
  }

  function streamTask(id: string) {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    const started = performance.now()

    void fetchEventSource(`${API_BASE}/tasks/${id}/events`, {
      headers: securityHeaders(),
      signal: controller.signal,
      openWhenHidden: true,
      onmessage(message) {
        if (!message.data) return
        let payload: TaskEvent
        try {
          payload = JSON.parse(message.data) as TaskEvent
        } catch {
          return
        }
        const event = { ...payload, received_at: performance.now() - started }
        setEvents((previous) => [...previous, event])
        if (event.event_type === 'human_review_required') {
          // The task is parked at HITL; the SSE stream stays open by design, but
          // the run is no longer "in flight" for the UI.
          controller.abort()
          setRunning(false)
          void queryClient.invalidateQueries({ queryKey: queryKeys.task(id) })
          void queryClient.invalidateQueries({ queryKey: queryKeys.approvals })
          void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard })
          return
        }
        if (event.event_type === 'task_completed' || event.event_type === 'task_failed') {
          controller.abort()
          setRunning(false)
          void queryClient.invalidateQueries({ queryKey: queryKeys.task(id) })
          void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard })
        }
      },
      onclose() {
        setRunning(false)
      },
      onerror(error) {
        if (controller.signal.aborted) return
        setStreamError(String(error))
        setRunning(false)
        // Throwing stops fetch-event-source's automatic retry loop.
        throw error
      },
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return
      setStreamError(String(error))
      setRunning(false)
    })
  }

  async function run() {
    abortRef.current?.abort()
    setEvents([])
    setResult(undefined)
    setTaskId(undefined)
    setStreamError(undefined)
    setRunning(true)

    try {
      // A Mock Case is a fixture only while its text is unchanged. Edited text is a
      // real customer message sent with the selected Security Context.
      const runAsMockCase = mode === 'mock' && selectedCase?.message === text
      const response = runAsMockCase
        ? await runMock.mutateAsync(caseKey)
        : await runChat.mutateAsync(text)

      setResult(response)
      if (response.task_id) {
        setTaskId(response.task_id)
        streamTask(response.task_id)
      } else {
        setEvents([
          {
            id: 0,
            event_type: 'direct_result',
            created_at: new Date().toISOString(),
            received_at: 0,
            node: response.mode,
            output: response,
          },
        ])
        setRunning(false)
        void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard })
      }
    } catch (error) {
      setRunning(false)
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }

  const effectiveResult = result
    ? ({ ...result, ...(task.data?.result as ChatResponse | undefined) } as ChatResponse)
    : undefined
  const traceId = (effectiveResult?.trace_id as string | undefined) ?? task.data?.trace_id ?? undefined

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Customer Service Agent Lab
          </Typography.Title>
          <Typography.Text type="secondary">
            Security Context：{user} / {tenant} / {role} · 当前上下文：{mode === 'mock' ? 'Mock Case' : 'Real 请求'}
          </Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void cases.refetch()} loading={cases.isFetching}>
          刷新 Case
        </Button>
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={9}>
          <Card title="请求">
            <Space orientation="vertical" style={{ width: '100%' }} size="middle">
              <Select
                style={{ width: '100%' }}
                value={caseKey}
                onChange={choose}
                disabled={mode === 'real'}
                loading={cases.isLoading}
                options={(cases.data ?? []).map((item) => ({
                  value: item.case_key,
                  label: `${item.title} · ${item.user_id}`,
                }))}
              />
              {selectedCase && mode === 'mock' ? (
                <Alert type="info" showIcon title={selectedCase.message} />
              ) : null}
              <Input.TextArea
                rows={7}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="输入客户消息，例如：订单 O10002 还没发货，我要退款"
              />
              {mode === 'mock' && selectedCase && text !== selectedCase.message ? (
                <Typography.Text type="warning" style={{ fontSize: 12 }}>
                  文本已修改，将按当前 Security Context 发送自定义请求。
                </Typography.Text>
              ) : null}
              <Button
                type="primary"
                block
                icon={<PlayCircleOutlined />}
                onClick={() => void run()}
                loading={running || runMock.isPending || runChat.isPending}
              >
                运行客服请求
              </Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Workflow 任务由独立 Worker 消费；本地演示请另开终端运行
                <Typography.Text code>python -m app.worker</Typography.Text>。
              </Typography.Text>
            </Space>
          </Card>

          {effectiveResult ? (
            <Card
              title="结果"
              style={{ marginTop: 16 }}
              extra={
                traceId ? <Link to={`/traces?trace_id=${encodeURIComponent(traceId)}`}>查看 Trace</Link> : null
              }
            >
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="Mode">{String(effectiveResult.mode ?? '-')}</Descriptions.Item>
                <Descriptions.Item label="Intent">{String(effectiveResult.intent ?? '-')}</Descriptions.Item>
                <Descriptions.Item label="Task">{taskId ?? '-'}</Descriptions.Item>
                <Descriptions.Item label="Trace">{traceId ?? '-'}</Descriptions.Item>
                {effectiveResult.answer ? (
                  <Descriptions.Item label="回答">{String(effectiveResult.answer)}</Descriptions.Item>
                ) : null}
              </Descriptions>
              <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 280, overflow: 'auto', marginTop: 12 }}>
                {JSON.stringify(effectiveResult, null, 2)}
              </pre>
            </Card>
          ) : null}
        </Col>

        <Col xs={24} lg={15}>
          <Card
            title={
              <Space>
                Workflow Timeline
                {task.data?.status ? <Tag color={task.data.status === 'failed' ? 'red' : 'blue'}>{task.data.status}</Tag> : null}
                {task.data?.current_step ? <Tag>{task.data.current_step}</Tag> : null}
              </Space>
            }
            extra={
              task.data?.trace_id ? (
                <Link to={`/traces?trace_id=${encodeURIComponent(task.data.trace_id)}`}>Trace 回放</Link>
              ) : null
            }
          >
            {streamError ? (
              <Alert
                type="error"
                showIcon
                style={{ marginBottom: 12 }}
                title="SSE 连接中断"
                description={streamError}
              />
            ) : null}
            {timeline.length ? (
              <Timeline
                items={timeline.map(({ event, elapsed, delta }) => ({
                  color: eventColor(event.event_type),
                  content: <TimelineEvent event={event} elapsed={elapsed} delta={delta} />,
                }))}
              />
            ) : (
              <Empty description={running ? '等待 Worker 产出事件…' : '选择 Mock Case 并运行，时间线会实时出现'} />
            )}
          </Card>
        </Col>
      </Row>
    </>
  )
}
