import { create } from 'zustand'
type S={mock:boolean;setMock:(v:boolean)=>void}
export const useRuntime=create<S>((set)=>({mock:true,setMock:(mock)=>set({mock})}))
