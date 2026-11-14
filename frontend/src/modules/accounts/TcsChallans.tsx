import { ChallanList, TCS } from "./Challans";

/** TCS paid over to the government: the TDS challan's screen, for tax collected. */
export default function TcsChallans() {
  return <ChallanList kind={TCS} />;
}
