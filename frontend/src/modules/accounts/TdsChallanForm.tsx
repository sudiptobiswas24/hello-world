import { ChallanForm, TDS } from "./Challans";

/** A month's deductions under one section paid over. */
export default function TdsChallanForm() {
  return <ChallanForm kind={TDS} />;
}
