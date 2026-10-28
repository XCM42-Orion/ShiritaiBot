import { InstanceCollection } from "../components/InstanceCollection";
import { ADAPTER_FIELDS, createAdapter } from "../config/schema";
import type { ConfigPageProps } from "./common";

export function AdaptersPage({ config, setConfig }: ConfigPageProps) {
  return (
    <InstanceCollection
      title="OneBot V11 实例"
      items={config.adapters}
      create={() => createAdapter(config.adapters)}
      onChange={(adapters) => setConfig({ ...config, adapters })}
      fields={ADAPTER_FIELDS}
      subtitle={(item) => item.url}
      emptyText="添加第一个实例，连接 NapCat。"
    />
  );
}
