import { Alert, Button, Empty, Space, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

export function errorText(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

interface ErrorAlertProps {
  title: string
  error: unknown
  onRetry?: () => void
}

/** Consistent API failure surface: readable reason plus an explicit retry action. */
export function ErrorAlert({ title, error, onRetry }: ErrorAlertProps) {
  return (
    <Alert
      type="error"
      showIcon
      title={title}
      description={
        <Space orientation="vertical" size={4}>
          <Typography.Text>{errorText(error)}</Typography.Text>
          {onRetry ? (
            <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
              重试
            </Button>
          ) : null}
        </Space>
      }
      style={{ marginBottom: 16 }}
    />
  )
}

interface EmptyHintProps {
  filtered: boolean
  emptyText: string
  filteredText?: string
}

/** Distinguishes "no data at all" from "filters excluded everything". */
export function EmptyHint({ filtered, emptyText, filteredText }: EmptyHintProps) {
  return <Empty description={filtered ? (filteredText ?? '没有匹配的数据，试试调整过滤条件') : emptyText} />
}

interface QueryEmptyProps extends EmptyHintProps {
  isError: boolean
  loading: boolean
}

/** Table empty state that never claims "no data" while loading or after a failure. */
export function QueryEmpty({ isError, loading, filtered, emptyText, filteredText }: QueryEmptyProps) {
  if (isError) return <Empty description="数据加载失败，请点击上方「重试」重新加载。" />
  if (loading) return <Empty description="正在加载…" />
  return <EmptyHint filtered={filtered} emptyText={emptyText} filteredText={filteredText} />
}
