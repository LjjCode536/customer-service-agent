import {useEffect,useState} from 'react';
import {Card,Space,Table,Tag,Typography} from 'antd';
import {api} from '../api/client';

type Check={field:string;expected:any;actual:any;passed:boolean};
type Case={id:string;name:string;category:string;passed:boolean;error?:string;trace_id?:string;checks:Check[];expected:any;actual:any};
type Report={summary?:{total:number;passed:number;failed:number;pass_rate:number};cases:Case[]};

const empty:Report={cases:[]};

export default function Evaluation(){
  const[report,setReport]=useState<Report>(empty);
  useEffect(()=>{api.get('/evaluation').then(r=>setReport(r.data))},[]);
  const failed=(c:Case)=>c.checks.filter(x=>!x.passed);
  return <>
    <Typography.Title level={3}>Golden Dataset Evaluation</Typography.Title>
    <Card style={{marginBottom:16}}>
      <Space>
        {report.summary && <Tag color={report.summary.failed?'red':'green'}>
          PASS {report.summary.passed}/{report.summary.total}
        </Tag>}
        <Typography.Text type="secondary">每个用例执行完整 Task / Worker / Workflow 链路，使用离线 Provider 与隔离数据库。</Typography.Text>
      </Space>
    </Card>
    <Table rowKey="id" dataSource={report.cases} pagination={false}
      expandable={{expandedRowRender:(r:Case)=><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify({expected:r.expected,actual:r.actual,error:r.error},null,2)}</pre>}}
      columns={[
        {title:'Case',dataIndex:'name'},
        {title:'Category',dataIndex:'category'},
        {title:'结果',dataIndex:'passed',render:(x:boolean)=><Tag color={x?'green':'red'}>{x?'PASS':'FAIL'}</Tag>},
        {title:'失败断言',render:(_:unknown,r:Case)=>failed(r).length?failed(r).map(c=><Tag color="red" key={c.field}>{c.field}</Tag>):'-'},
        {title:'Trace',dataIndex:'trace_id',render:(x?:string)=>x||'-'},
      ]}/>
  </>;
}
