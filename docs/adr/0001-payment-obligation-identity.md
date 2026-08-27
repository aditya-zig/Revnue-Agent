# Payment obligation identity

ReRoute groups payment attempts by an explicit, verified merchant order, invoice, or subscription reference. It creates one permanent RecoveryCase for that PaymentObligation and never infers a link from Customer, amount, or timing. This prevents duplicate recovery contact when a later attempt succeeds or a Customer reports a debit.
