# HCP Terraform -> AWS with no stored keys (dynamic provider credentials).
# Two roles, split by run phase, the production pattern:
#   plan  -> ReadOnlyAccess  (speculative plans on every PR cannot change anything)
#   apply -> AdministratorAccess, only after a human confirms the plan
# Both trust ONLY this organization + project's workspaces.
# ref: developer.hashicorp.com/terraform/cloud-docs/workspaces/dynamic-provider-credentials/aws-configuration
#
# AdministratorAccess is broad: these stacks create IAM roles, Lake
# Formation settings, SMUS and AgentCore. Tighten with a permissions
# boundary before anything beyond a POC.

resource "aws_iam_openid_connect_provider" "hcp" {
  url            = "https://app.terraform.io"
  client_id_list = ["aws.workload.identity"]
}

data "aws_iam_policy_document" "hcp_trust" {
  for_each = toset(["plan", "apply"])
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.hcp.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "app.terraform.io:aud"
      values   = ["aws.workload.identity"]
    }
    condition {
      test     = "StringLike"
      variable = "app.terraform.io:sub"
      values   = ["organization:${var.tfc_organization}:project:${var.tfc_project}:workspace:*:run_phase:${each.key}"]
    }
  }
}

resource "aws_iam_role" "hcp" {
  for_each             = toset(["plan", "apply"])
  name                 = "hcp-terraform-${var.tfc_project}-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.hcp_trust[each.key].json
  max_session_duration = 7200
}

resource "aws_iam_role_policy_attachment" "hcp" {
  for_each = {
    plan  = "arn:aws:iam::aws:policy/ReadOnlyAccess"
    apply = "arn:aws:iam::aws:policy/AdministratorAccess"
  }
  role       = aws_iam_role.hcp[each.key].name
  policy_arn = each.value
}

# Plans read Lake Formation, SSM SecureString and KMS-encrypted state of
# resources these stacks manage; ReadOnlyAccess covers describe/get but
# not LF permission listing.
resource "aws_iam_role_policy" "plan_extras" {
  role = aws_iam_role.hcp["plan"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lakeformation:GetDataLakeSettings", "lakeformation:ListPermissions", "lakeformation:GetLFTag", "lakeformation:GetResourceLFTags", "lakeformation:ListLFTags", "lakeformation:DescribeResource", "kms:Decrypt"]
      Resource = "*"
    }]
  })
}
