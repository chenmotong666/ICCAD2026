module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n12,
    n11,
    n6,
    n7,
    n8,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output [3:0] n12;
  output n11;
  output n6;
  output n7;
  output n8;
  output n9;
  output n10;

  wire \$and$testcase/test83/test83.v:29$12_Y , \$and$testcase/test83/test83.v:33$17_Y , __fo_n2_2__0__, __fo_n2_2__1__, __fo_n2_2__2__, __fo_n2_2__3__, __fo_n3_2__0__, __fo_n3_2__1__, __fo_n3_2__2__, __fo_n5_0__, __fo_n5_1__, __fo_n5_2__, __fo_n5_3__, n13, n14, n15, n16, n17, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n56, n57, n63, n65, n80, n81, n82;

  and   \$and$testcase/test83/test83.v:53$28  (n80, n2[0], n2[1]);
  buf   fo_buf_0 (__fo_n2_2__0__, n2[2]);
  buf   fo_buf_1 (__fo_n2_2__1__, n2[2]);
  buf   fo_buf_2 (__fo_n2_2__2__, n2[2]);
  buf   fo_buf_3 (__fo_n2_2__3__, n2[2]);
  and   \$and$testcase/test83/test83.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test83/test83.v:23$6  (n30, n2[1], n3[1]);
  buf   fo_buf_4 (__fo_n3_2__0__, n3[2]);
  buf   fo_buf_5 (__fo_n3_2__1__, n3[2]);
  buf   fo_buf_6 (__fo_n3_2__2__, n3[2]);
  not   \$not$testcase/test83/test83.v:44$32  (n63, n4);
  buf   fo_buf_7 (__fo_n5_0__, n5);
  buf   fo_buf_8 (__fo_n5_1__, n5);
  buf   fo_buf_9 (__fo_n5_2__, n5);
  buf   fo_buf_10 (__fo_n5_3__, n5);
  or    \$or$testcase/test83/test83.v:54$29  (n81, n80, n3[0]);
  or    \$or$testcase/test83/test83.v:17$2  (n14, n13, n4);
  and   \$and$testcase/test83/test83.v:26$9  (n40, __fo_n2_2__0__, __fo_n3_2__0__);
  and   \$and$testcase/test83/test83.v:30$14  (n44, __fo_n2_2__0__, __fo_n3_2__0__);
  and   \$and$testcase/test83/test83.v:34$19  (n48, __fo_n2_2__1__, __fo_n3_2__1__);
  xor   \$xor$testcase/test83/test83.v:28$11  (n42, __fo_n2_2__2__, __fo_n3_2__1__);
  xor   \$xor$testcase/test83/test83.v:32$16  (n46, __fo_n2_2__3__, __fo_n3_2__2__);
  and   \$and$testcase/test83/test83.v:29$12  (\$and$testcase/test83/test83.v:29$12_Y , __fo_n2_2__0__, __fo_n5_0__);
  and   \$and$testcase/test83/test83.v:33$17  (\$and$testcase/test83/test83.v:33$17_Y , __fo_n2_2__1__, __fo_n5_0__);
  or    \$or$testcase/test83/test83.v:27$10  (n41, __fo_n2_2__1__, __fo_n5_1__);
  or    \$or$testcase/test83/test83.v:31$15  (n45, __fo_n2_2__2__, __fo_n5_1__);
  or    \$or$testcase/test83/test83.v:35$20  (n49, __fo_n2_2__2__, __fo_n5_2__);
  xor   \$xor$testcase/test83/test83.v:24$7  (n31, n30, __fo_n5_3__);
  not   \$not$testcase/test83/test83.v:55$34  (n82, n81);
  not   \$not$testcase/test83/test83.v:18$30  (n15, n14);
  not   \$not$testcase/test83/test83.v:29$13  (n43, \$and$testcase/test83/test83.v:29$12_Y );
  not   \$not$testcase/test83/test83.v:33$18  (n47, \$and$testcase/test83/test83.v:33$17_Y );
  or    \$or$testcase/test83/test83.v:36$21  (n56, n40, n41);
  or    \$or$testcase/test83/test83.v:25$8  (n7, n31, n4);
  xor   \$xor$testcase/test83/test83.v:19$3  (n16, n15, __fo_n5_2__);
  or    \$or$testcase/test83/test83.v:37$22  (n57, n56, n42);
  and   \$and$testcase/test83/test83.v:20$4  (n17, n16, n2[1]);
  or    \$or$testcase/test83/test83.v:38$23  (n8, n57, n43);
  or    \$or$testcase/test83/test83.v:21$5  (n6, n17, n3[1]);
  and   \$and$testcase/test83/test83.v:47$27  (n65, n31, n8);
  dff g32 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));

  assign n12[0] = n6;
  assign n12[1] = n7;
  assign n12[2] = n8;
  assign n9 = 1'b1;
  assign n10 = n4;
  assign n12[3] = 1'b1;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
