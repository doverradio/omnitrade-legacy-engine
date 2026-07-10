import CapitalCampaignDetailCenter from "@/components/domain/CapitalCampaignDetailCenter";

export default function CapitalCampaignDetailPage({ params }: { params: { campaignUuid: string } }) {
  return <CapitalCampaignDetailCenter campaignUuid={params.campaignUuid} />;
}
