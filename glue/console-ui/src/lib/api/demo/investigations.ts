/* Sample answers for the Investigate panel. In the kit the console gathers the same evidence from
   the tools' APIs (read-only) and asks the configured model for the draft; here the evidence and
   the draft are written out for each sample problem. */
import type { InvestigationStep, NextStep, Problem } from "../types";
import { toolUrl } from "./sample";

export interface Script {
  steps: Omit<InvestigationStep, "status">[];
  cause: string;
  confidence: "low" | "medium" | "high";
  nextSteps: NextStep[];
  note: string;
}

type Step = Omit<InvestigationStep, "status">;
const step = (id: string, tool: Step["tool"], title: string, finding: string, link: string | null = null): Step =>
  ({ id, tool, title, finding, link });

function scripts(p: Problem): Record<string, Script> {
  const zbx = toolUrl("zabbix", `/zabbix.php?action=problem.view&filter_name=${encodeURIComponent(p.device)}`);
  const nb = toolUrl("nautobot", `/dcim/devices/nb-${p.device}/`);
  return {
    "40817": {
      steps: [
        step("z1", "Zabbix", "Read 2 hours of ICMP, SNMP and interface values for bks01-rtr01",
          "Packet loss went from 0 % to 100 % at the same poll as SNMP stopped answering. CPU and memory were normal before it.", zbx),
        step("z2", "Zabbix", "Checked the other devices at the store and at ACME",
          "bks01-sw01, behind the router, went silent at the same minute. The other ACME sites are up."),
        step("n1", "Nautobot", "Read the site, its devices and circuits",
          "ACME Store Bekasi has one broadband circuit (ACME-BB-BKS-01) and no backup link.", nb),
        step("g1", "GLPI", "Looked for changes and tickets at the site in the last 7 days",
          "No change planned or applied. Ticket #1047 was opened by Zabbix a minute after the problem started.",
          toolUrl("glpi", "/front/ticket.form.php?id=1047")),
        step("f1", "Forgejo", "Compared the last two configuration backups",
          "No configuration change since 12 October."),
        step("s1", "Semaphore", "Checked automation runs on the device",
          "No task ran on bks01-rtr01 in the last 24 hours."),
        step("z3", "Zabbix", "Searched earlier problems on this device",
          "Two earlier outages of 50 and 35 minutes. Both tickets (#1008, #962) were closed as power cuts at the store."),
      ],
      cause: "Power or broadband loss at ACME Store Bekasi. Both devices at the store stopped answering at the same poll, nothing changed in the configuration or in automation, and the site's two earlier outages were store power cuts.",
      confidence: "medium",
      nextSteps: [
        { text: "Call the store (contact on ticket #1047) and ask whether the network cabinet and the broadband modem have power." },
        { text: "If they have power, report a fault on circuit ACME-BB-BKS-01 with the access team." },
        { text: "When the router answers again, collect its logs and uptime to confirm the cause.", playbook: { template: "Collect diagnostics", devices: ["bks01-rtr01"] } },
      ],
      note: "Investigation (draft, checked by the engineer before posting)\n\nLikely cause: power or broadband loss at ACME Store Bekasi.\n\nEvidence:\n- bks01-rtr01 and bks01-sw01 stopped answering ICMP and SNMP at the same poll.\n- No change, configuration difference or automation run on the site in the last 7 days.\n- The site's earlier outages (#1008, #962) were store power cuts.\n\nNext: call the store to check power to the cabinet and the modem; if powered, report a fault on circuit ACME-BB-BKS-01.",
    },
    "40809": {
      steps: [
        step("z1", "Zabbix", "Read 6 hours of CPU, sessions and traffic for ckr01-fw01",
          "CPU climbed from 41 % to 93 % within 12 minutes. Sessions rose from about 120 thousand to 410 thousand at the same time; traffic on port2 stayed normal.", zbx),
        step("z2", "Zabbix", "Compared with the cluster peer ckr01-fw02",
          "ckr01-fw02 is at 38 % CPU, as expected for the passive member."),
        step("n1", "Nautobot", "Read the device and its cluster",
          "FortiGate 200F, FortiOS 7.4.5, active-passive pair with ckr01-fw02.", nb),
        step("g1", "GLPI", "Looked for changes on the firewall in the last 7 days",
          "Change #62 (FortiOS 7.4.8 upgrade) is accepted for tonight. Nothing was applied this week.",
          toolUrl("glpi", "/front/change.form.php?id=62")),
        step("f1", "Forgejo", "Compared the last two configuration backups", "No configuration change since 11 October."),
        step("s1", "Semaphore", "Checked automation runs on the device", "None in the last 7 days."),
      ],
      cause: "A surge of new sessions through the active firewall. Sessions tripled while traffic stayed flat, which points to many short connections, for example a scan or a misbehaving client. The configuration has not changed since 11 October.",
      confidence: "medium",
      nextSteps: [
        { text: "Collect the top sessions by source address from the firewall.", playbook: { template: "Collect diagnostics", devices: ["ckr01-fw01"] } },
        { text: "If one source dominates, agree with the bank's security team before blocking it." },
        { text: "Review tonight's change #62 if CPU is still above 90 % an hour before the window." },
      ],
      note: "Investigation (draft, checked by the engineer before posting)\n\nLikely cause: a surge of short sessions through ckr01-fw01.\n\nEvidence:\n- CPU 41 % to 93 % in 12 minutes; sessions about 120k to 410k; traffic unchanged.\n- Passive peer ckr01-fw02 normal at 38 %.\n- No configuration change since 11 October, no automation runs this week.\n\nNext: collect top sessions by source; agree any block with the bank's security team; review change #62 before tonight's window.",
    },
    "40821": {
      steps: [
        step("z1", "Zabbix", "Read 7 days of disk use for /var/lib/pacs on jks01-srv01",
          "Use grew about 0.6 % a day until yesterday, then 16 % in the last 24 hours. At this rate the volume is full in about 5 hours.", zbx),
        step("g1", "GLPI", "Looked for changes and requests at the hospital this week",
          "Request #1033 connected a new CT scanner. Change #61 (imaging VLAN move) is in progress now.",
          toolUrl("glpi", "/front/ticket.form.php?id=1033")),
        step("n1", "Nautobot", "Read the server's record", "PACS gateway, Linux virtual machine, 500 GB volume for /var/lib/pacs.", nb),
        step("s1", "Semaphore", "Checked automation runs on the server", "Linux patching ran 12 days ago. Nothing since."),
        step("z2", "Zabbix", "Checked the other servers of the customer", "jks01-srv02 disk use is flat at 38 %."),
      ],
      cause: "The new CT scanner (request #1033) sends more studies than the gateway moves to long-term storage overnight, so the volume now fills during the day.",
      confidence: "medium",
      nextSteps: [
        { text: "Ask the hospital's PACS administrator to start the archive job now." },
        { text: "Find the largest folders on the volume.", playbook: { template: "Collect diagnostics", devices: ["jks01-srv01"] } },
        { text: "Grow the volume or archive more often, under a change agreed with the hospital." },
      ],
      note: "Investigation (draft, checked by the engineer before posting)\n\nLikely cause: the new CT scanner (request #1033) produces more studies than the nightly archive moves off jks01-srv01.\n\nEvidence:\n- /var/lib/pacs grew 16 % in 24 hours after growing 0.6 % a day before; full in about 5 hours at this rate.\n- No automation or configuration change on the server since patching 12 days ago.\n\nNext: ask the PACS administrator to run the archive job now; plan a volume increase or more frequent archiving under a change.",
    },
    "40788": {
      steps: [
        step("z1", "Zabbix", "Read the interfaces of mdn01-rtr01 for 3 hours",
          "Gi0/0/0 (MPLS primary) went down 130 minutes ago; Cellular0/2/0 (LTE backup) took the traffic within a minute.", zbx),
        step("n1", "Nautobot", "Read the circuits of Branch Medan", "MPLS circuit BANKX-MPLS-MDN-01 and LTE backup BANKX-LTE-MDN-01.", nb),
        step("g1", "GLPI", "Read ticket #1041 and its follow-ups", "Carrier fault reference C-55102 is recorded; status Waiting.",
          toolUrl("glpi", "/front/ticket.form.php?id=1041")),
        step("s1", "Semaphore", "Read task #320 (Collect diagnostics)", "The interface shows loss of signal from the carrier side; the router's configuration is unchanged."),
      ],
      cause: "Carrier fault on the MPLS circuit BANKX-MPLS-MDN-01. The router reports loss of signal on the line side and the LTE backup carries the branch.",
      confidence: "high",
      nextSteps: [
        { text: "Ask the carrier for an update on fault C-55102 before the ticket's due time." },
        { text: "Watch the LTE data volume: the branch has been on backup for over 2 hours." },
      ],
      note: "Investigation (draft, checked by the engineer before posting)\n\nLikely cause: carrier fault on BANKX-MPLS-MDN-01 (loss of signal on the line side). The branch runs on the LTE backup.\n\nNext: chase carrier fault C-55102; watch LTE usage.",
    },
    "40795": {
      steps: [
        step("z1", "Zabbix", "Read response time and Gi0/0/0 traffic for jkt01-rtr01",
          "Response time rose with the traffic on Gi0/0/0, which has been above 180 Mbit/s since the problem started.", zbx),
        step("n1", "Nautobot", "Read the uplink circuit", "ACME-DIA-JKT-01, 200 Mbit/s profile on a 1 Gbit/s port.", nb),
        step("s1", "Semaphore", "Read task #318 (Collect diagnostics)", "Output queue drops on Gi0/0/0, no errors on the interface."),
      ],
      cause: "The internet uplink of ACME HQ is full at peak time: traffic sits at the circuit's 200 Mbit/s profile and the router queues packets.",
      confidence: "high",
      nextSteps: [
        { text: "Share the traffic chart with the customer and propose a larger profile for ACME-DIA-JKT-01." },
        { text: "Check whether a backup or update job runs at midday and can move to the night." },
      ],
      note: "Investigation (draft, checked by the engineer before posting)\n\nLikely cause: uplink ACME-DIA-JKT-01 saturated at its 200 Mbit/s profile at peak time; output drops on Gi0/0/0.\n\nNext: propose a larger profile to the customer; look for midday bulk transfers.",
    },
    "40766": {
      steps: [
        step("z1", "Zabbix", "Read 30 days of memory use for ckr03-sw01", "Memory rose steadily for a week, from 66 % to 92 %, without a matching rise in traffic.", zbx),
        step("n1", "Nautobot", "Read the device", "Huawei S5735-L24T4X; software version recorded at installation.", nb),
        step("s1", "Semaphore", "Read task #314 (Collect diagnostics)", "One process holds most of the memory and grows over time."),
      ],
      cause: "A slow memory leak in the switch software, as noted on ticket #1043. A restart frees the memory for a few weeks; the upgrade fixes it.",
      confidence: "medium",
      nextSteps: [{ text: "Agree an upgrade window with the customer and open a change." }],
      note: "Investigation (draft, checked by the engineer before posting)\n\nLikely cause: slow memory leak in the switch software. Next: plan the upgrade under a change.",
    },
    "40812": {
      steps: [
        step("z1", "Zabbix", "Read error counters for krw01-sw02", "Errors are on port 1/0/24 only, the cable to krw01-sw01 port 1/0/26, which also counts errors.", zbx),
        step("g1", "GLPI", "Looked for open tickets on the device", "Request #1029 (replace the cable on port 24) is waiting for parts.",
          toolUrl("glpi", "/front/ticket.form.php?id=1029")),
      ],
      cause: "A damaged cable or patch between krw01-sw02 port 1/0/24 and krw01-sw01 port 1/0/26: both ends count errors and no other port does.",
      confidence: "high",
      nextSteps: [{ text: "Follow up request #1029 with the plant to replace the cable." }],
      note: "Investigation (draft, checked by the engineer before posting)\n\nLikely cause: damaged cable between krw01-sw02 1/0/24 and krw01-sw01 1/0/26. Next: replace the cable (request #1029).",
    },
    "40819": {
      steps: [
        step("z1", "Zabbix", "Read the maintenance windows of the device", "jks01-sw02 is in maintenance 'Change #61: imaging VLAN move' until the change window ends.", zbx),
        step("g1", "GLPI", "Read change #61", "The change moves the imaging modalities to VLAN 40; the switch reloads during the work.",
          toolUrl("glpi", "/front/change.form.php?id=61")),
      ],
      cause: "Expected during change #61: the switch is being reloaded as part of the VLAN move.",
      confidence: "high",
      nextSteps: [{ text: "If the switch is still down when the window ends, ask the engineer on change #61 for the status." }],
      note: "Investigation (draft, checked by the engineer before posting)\n\nExpected outage during change #61 (imaging VLAN move); the switch reloads during the work.",
    },
  };
}

export function scriptFor(problem: Problem): Script {
  const known = scripts(problem)[problem.eventid];
  if (known) return known;
  return {
    steps: [
      step("z1", "Zabbix", `Read 2 hours of values for ${problem.device}`, "Nothing unusual before the problem started."),
      step("n1", "Nautobot", "Read the device and its site", `${problem.device} at ${problem.site}.`),
      step("g1", "GLPI", "Looked for changes in the last 7 days", "None."),
    ],
    cause: "Not enough evidence for a likely cause yet.",
    confidence: "low",
    nextSteps: [{ text: "Collect diagnostics from the device.", playbook: { template: "Collect diagnostics", devices: [problem.device] } }],
    note: "Investigation (draft): not enough evidence for a likely cause yet.",
  };
}
