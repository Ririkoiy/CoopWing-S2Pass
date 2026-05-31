import 'package:flutter/material.dart';

class ContentColumnPage extends StatelessWidget {
  const ContentColumnPage({
    super.key,
    required this.child,
    this.maxWidth = 920,
    this.horizontalPadding = 28,
    this.verticalPadding = 28,
    this.bottomPadding,
    this.alignment = Alignment.topCenter,
  });

  final Widget child;
  final double maxWidth;
  final double horizontalPadding;
  final double verticalPadding;
  final double? bottomPadding;
  final Alignment alignment;

  @override
  Widget build(BuildContext context) {
    final resolvedBottomPadding = bottomPadding ?? verticalPadding;

    return SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final availableWidth = constraints.maxWidth - (horizontalPadding * 2);
          final contentWidth = availableWidth <= 0
              ? 0.0
              : availableWidth < maxWidth
              ? availableWidth
              : maxWidth;

          return SingleChildScrollView(
            padding: EdgeInsets.fromLTRB(
              horizontalPadding,
              verticalPadding,
              horizontalPadding,
              resolvedBottomPadding,
            ),
            child: Align(
              alignment: alignment,
              child: SizedBox(width: contentWidth, child: child),
            ),
          );
        },
      ),
    );
  }
}
