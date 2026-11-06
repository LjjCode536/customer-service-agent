import { Button, Result } from 'antd'
import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <Result
      status="404"
      title="404"
      subTitle="页面不存在或链接已失效。"
      extra={
        <Link to="/">
          <Button type="primary">返回运行总览</Button>
        </Link>
      }
    />
  )
}
