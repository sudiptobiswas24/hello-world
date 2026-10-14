import { ChallanForm, TCS } from "./Challans";

/** What was collected under one section paid over, net of what credit notes gave back. */
export default function TcsChallanForm() {
  return <ChallanForm kind={TCS} />;
}
