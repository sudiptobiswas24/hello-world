import { ChallanList, TDS } from "./Challans";

/** TDS paid over to the government, a month and a section at a time. */
export default function TdsChallans() {
  return <ChallanList kind={TDS} />;
}
